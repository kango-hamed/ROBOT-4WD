"""Synthèse vocale : moteurs, cache et lecture — sans ROS.

Ordre des moteurs : Edge TTS (voix neuronale en ligne, gratuite mais non officielle) puis
Piper (voix neuronale locale, fonctionne sans réseau sur le Pi 5). Le cache est consulté
avant toute synthèse, pour tous les moteurs : une phrase déjà dite en ligne est rejouée
à l'identique hors ligne (accueil, présentations, fin de visite).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import subprocess
import wave
from pathlib import Path


class TtsError(RuntimeError):
    pass


class SpeechCache:
    def __init__(self, directory: str) -> None:
        self.dir = Path(directory).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, engine_id: str, text: str, ext: str) -> Path:
        h = hashlib.sha1(f'{engine_id}\n{text}'.encode('utf-8')).hexdigest()[:20]
        return self.dir / f'{h}{ext}'

    def get(self, engine_id: str, text: str, ext: str) -> Path | None:
        p = self.path(engine_id, text, ext)
        return p if p.is_file() and p.stat().st_size > 0 else None


class EdgeTts:
    ext = '.mp3'

    def __init__(self, voice: str = 'fr-FR-DeniseNeural', rate: str = '-5%', timeout_s: float = 8.0) -> None:
        self.voice, self.rate, self.timeout = voice, rate, timeout_s
        self.id = f'edge:{voice}:{rate}'

    def synthesize(self, text: str, out: Path) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise TtsError('edge-tts non installé (pip install edge-tts)') from exc

        async def run():
            await edge_tts.Communicate(text, self.voice, rate=self.rate).save(str(out))
        try:
            asyncio.run(asyncio.wait_for(run(), self.timeout))
        except Exception as exc:          # réseau absent, service refusé, délai dépassé…
            out.unlink(missing_ok=True)
            raise TtsError(f'Edge TTS : {exc}') from exc


class PiperTts:
    ext = '.wav'

    def __init__(self, model: str, binary: str = 'piper') -> None:
        self.model, self.binary = os.path.expanduser(model), binary
        self.id = f'piper:{Path(model).stem}'

    def synthesize(self, text: str, out: Path) -> None:
        if not os.path.isfile(self.model):
            raise TtsError(f'modèle Piper introuvable : {self.model}')
        try:
            subprocess.run([self.binary, '--model', self.model, '--output_file', str(out)],
                           input=text.encode('utf-8'), capture_output=True, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            out.unlink(missing_ok=True)
            raise TtsError(f'Piper : {exc}') from exc


class Voice:
    """Choisit le fichier à jouer pour un texte : cache d'abord, puis synthèse."""

    def __init__(self, engines: list, cache: SpeechCache) -> None:
        if not engines:
            raise ValueError('aucun moteur de synthèse')
        self.engines, self.cache = engines, cache

    def render(self, text: str) -> tuple[Path, str]:
        """Renvoie (fichier audio, origine). Lève TtsError si aucun moteur n'y arrive."""
        for e in self.engines:
            hit = self.cache.get(e.id, text, e.ext)
            if hit:
                return hit, f'cache {e.id}'
        errors = []
        for e in self.engines:
            out = self.cache.path(e.id, text, e.ext)
            try:
                e.synthesize(text, out)
                if out.is_file() and out.stat().st_size > 0:
                    return out, e.id
                errors.append(f'{e.id} : fichier vide')
            except TtsError as exc:
                errors.append(str(exc))
        raise TtsError(' ; '.join(errors))


def player_command(path: Path, device: str = 'default') -> list[str]:
    if path.suffix == '.mp3':
        return ['mpg123', '-q', '-o', 'alsa', '-a', device, str(path)]
    return ['aplay', '-q', '-D', device, str(path)]


def wav_duration_s(path: Path) -> float | None:
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, OSError, EOFError):
        return None
