"""Écoute : détection de parole et découpage en phrases — sans ROS.

Le micro I²S (INMP441) fournit un flux continu, lu par trames de 30 ms en PCM 16 bits mono.
Chaque trame est déclarée « voix » ou « silence » (WebRTC VAD si disponible, sinon un seuil
d'énergie adaptatif), puis le Segmenter reconstitue les phrases :
  * début : start_frames trames de voix consécutives (évite les claquements) ;
  * fin : end_frames trames de silence ;
  * pre_roll : on garde un peu d'audio avant le début, pour ne pas couper la première syllabe.
"""

from __future__ import annotations

import io
import math
import struct
import wave
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class SegmenterConfig:
    sample_rate: int = 16000
    frame_ms: int = 30
    start_frames: int = 5          # 150 ms de voix pour déclencher
    end_frames: int = 25           # 750 ms de silence pour conclure
    pre_roll_frames: int = 10      # 300 ms gardées avant le déclenchement
    max_s: float = 12.0            # au-delà, la phrase est coupée et envoyée
    min_s: float = 0.4             # plus court : bruit, ignoré

    @property
    def frame_bytes(self) -> int:
        return self.sample_rate * self.frame_ms // 1000 * 2


class Segmenter:
    def __init__(self, cfg: SegmenterConfig = SegmenterConfig()) -> None:
        self.cfg = cfg
        self._pre: deque[bytes] = deque(maxlen=cfg.pre_roll_frames + cfg.start_frames)
        self._buf: list[bytes] = []
        self._voiced_run = 0
        self._silence_run = 0
        self.active = False

    def reset(self) -> None:
        self._pre.clear()
        self._buf.clear()
        self._voiced_run = self._silence_run = 0
        self.active = False

    def feed(self, frame: bytes, voiced: bool) -> bytes | None:
        """Renvoie le PCM d'une phrase complète quand elle se termine, sinon None."""
        c = self.cfg
        if not self.active:
            self._pre.append(frame)
            self._voiced_run = self._voiced_run + 1 if voiced else 0
            if self._voiced_run >= c.start_frames:
                self.active = True
                self._buf = list(self._pre)
                self._pre.clear()
                self._silence_run = 0
            return None
        self._buf.append(frame)
        self._silence_run = 0 if voiced else self._silence_run + 1
        too_long = len(self._buf) * c.frame_ms / 1000 >= c.max_s
        if self._silence_run >= c.end_frames or too_long:
            frames = self._buf if too_long else self._buf[:-self._silence_run]
            pcm = b''.join(frames)
            self.reset()
            if len(pcm) / 2 / c.sample_rate < c.min_s:
                return None
            return pcm
        return None


class EnergyVad:
    """Seuil d'énergie qui suit le bruit de fond (ventilateur, salle)."""

    def __init__(self, ratio: float = 3.0, min_rms: float = 300.0, alpha: float = 0.05,
                 alpha_up: float = 0.0005) -> None:
        self.ratio, self.min_rms, self.alpha, self.alpha_up = ratio, min_rms, alpha, alpha_up
        self.noise = min_rms / ratio

    @staticmethod
    def rms(frame: bytes) -> float:
        n = len(frame) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f'<{n}h', frame[:n * 2])
        return math.sqrt(sum(s * s for s in samples) / n)

    def is_speech(self, frame: bytes) -> bool:
        level = self.rms(frame)
        voiced = level > max(self.min_rms, self.noise * self.ratio)
        # Hors parole, le fond suit vite. Pendant la « parole », il monte très lentement :
        # un bruit constant (ventilateur) finit par devenir la référence (~8 s), alors qu'une
        # phrase de 12 s ne relève le fond que d'environ 20 %.
        self.noise += (self.alpha_up if voiced else self.alpha) * (level - self.noise)
        return voiced


def to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return out.getvalue()


def worth_sending(text: str) -> bool:
    """Écarte les transcriptions vides ou parasites (Whisper « entend » parfois des formules)."""
    t = text.strip().strip('.!?…').strip().lower()
    if len(t) < 3:
        return False
    return t not in {'merci', 'sous-titres réalisés par la communauté d\'amara.org', 'merci d\'avoir regardé',
                     'sous-titrage st\' 501'}
