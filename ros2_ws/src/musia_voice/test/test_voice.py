import json
import math
import struct
import wave
import io

import pytest

from musia_voice import listening as li
from musia_voice import speech as sp
from musia_voice import transcription as tr

CFG = li.SegmenterConfig()
SILENCE = b'\x00\x00' * (CFG.frame_bytes // 2)


def tone(amplitude=8000):
    n = CFG.frame_bytes // 2
    return struct.pack(f'<{n}h', *(int(amplitude * math.sin(i / 3)) for i in range(n)))


# --- découpage des phrases --------------------------------------------------------------
def run(seg, pattern):
    out = []
    for voiced in pattern:
        r = seg.feed(tone() if voiced else SILENCE, voiced)
        if r:
            out.append(r)
    return out


def test_phrase_is_cut_after_silence_with_pre_roll():
    seg = li.Segmenter(CFG)
    out = run(seg, [False] * 20 + [True] * 30 + [False] * CFG.end_frames)
    assert len(out) == 1
    frames = len(out[0]) // CFG.frame_bytes
    # 30 trames de voix + jusqu'à pre_roll trames de silence avant, sans le silence final
    assert 30 <= frames <= 30 + CFG.pre_roll_frames


def test_short_click_is_ignored():
    seg = li.Segmenter(CFG)
    assert run(seg, [True] * (CFG.start_frames - 1) + [False] * 40) == []
    assert not seg.active


def test_too_short_phrase_is_dropped():
    seg = li.Segmenter(CFG)
    assert run(seg, [True] * CFG.start_frames + [False] * CFG.end_frames) == []


def test_long_speech_is_cut_at_max():
    seg = li.Segmenter(CFG)
    out = run(seg, [True] * 1000)
    max_frames = int(CFG.max_s * 1000 / CFG.frame_ms)
    assert out and all(len(o) // CFG.frame_bytes <= max_frames for o in out)


def test_energy_vad_adapts_to_noise():
    vad = li.EnergyVad(ratio=3, min_rms=300)
    assert vad.is_speech(tone(8000))
    assert not vad.is_speech(SILENCE)
    noise = tone(600)                   # au-dessus du seuil minimal : d'abord pris pour de la voix
    assert vad.is_speech(noise)
    for _ in range(int(10_000 / CFG.frame_ms)):     # 10 s de bruit constant
        vad.is_speech(noise)
    assert not vad.is_speech(noise)     # il est devenu la référence
    assert vad.is_speech(tone(8000))


def test_energy_vad_keeps_long_speech():
    vad = li.EnergyVad()
    for _ in range(50):
        vad.is_speech(SILENCE)
    speech = tone(5000)
    assert all(vad.is_speech(speech) for _ in range(int(CFG.max_s * 1000 / CFG.frame_ms)))


def test_wav_header():
    w = wave.open(io.BytesIO(li.to_wav(SILENCE * 10)))
    assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)


@pytest.mark.parametrize('text,ok', [('Qui a sculpté ce masque ?', True), ('', False), ('Merci.', False),
                                     ("Sous-titres réalisés par la communauté d'Amara.org", False)])
def test_worth_sending(text, ok):
    assert li.worth_sending(text) is ok


# --- synthèse et cache ------------------------------------------------------------------
class FakeEngine:
    def __init__(self, id_, fail=False, ext='.wav'):
        self.id, self.fail, self.ext, self.calls = id_, fail, ext, 0

    def synthesize(self, text, out):
        self.calls += 1
        if self.fail:
            raise sp.TtsError(f'{self.id} hors service')
        out.write_bytes(b'RIFF....')


def test_voice_falls_back_and_caches(tmp_path):
    online, local = FakeEngine('edge', fail=True, ext='.mp3'), FakeEngine('piper')
    v = sp.Voice([online, local], sp.SpeechCache(str(tmp_path)))
    path, origin = v.render('Bonjour')
    assert origin == 'piper' and path.exists()
    path2, origin2 = v.render('Bonjour')
    assert path2 == path and origin2.startswith('cache') and local.calls == 1


def test_cached_online_voice_is_preferred_offline(tmp_path):
    cache = sp.SpeechCache(str(tmp_path))
    online = FakeEngine('edge', ext='.mp3')
    sp.Voice([online], cache).render('Bienvenue')            # dit une fois en ligne
    online.fail = True                                      # puis le réseau tombe
    local = FakeEngine('piper')
    path, origin = sp.Voice([online, local], cache).render('Bienvenue')
    assert path.suffix == '.mp3' and origin == 'cache edge' and local.calls == 0


def test_voice_all_engines_fail(tmp_path):
    with pytest.raises(sp.TtsError):
        sp.Voice([FakeEngine('a', fail=True)], sp.SpeechCache(str(tmp_path))).render('x')


def test_player_command():
    assert sp.player_command(sp.Path('a.mp3'))[0] == 'mpg123'
    assert sp.player_command(sp.Path('a.wav'), 'hw:1,0')[:4] == ['aplay', '-q', '-D', 'hw:1,0']


# --- transcription ----------------------------------------------------------------------
def test_multipart_contains_fields_and_file():
    body, ctype = tr.GroqWhisper.multipart({'model': 'm', 'language': 'fr'}, b'WAVDATA')
    boundary = ctype.split('boundary=')[1]
    assert body.count(boundary.encode()) == 4
    assert b'name="language"\r\n\r\nfr' in body and b'WAVDATA' in body


def test_groq_without_key_fails_cleanly():
    with pytest.raises(tr.SttError):
        tr.GroqWhisper(api_key='').transcribe(b'')


def test_transcribe_first_uses_fallback():
    class Ok:
        id = 'local'

        def transcribe(self, wav):
            return 'bonjour'
    assert tr.transcribe_first([tr.GroqWhisper(api_key=''), Ok()], b'') == ('bonjour', 'local')
    assert json  # module importé pour les tests futurs
