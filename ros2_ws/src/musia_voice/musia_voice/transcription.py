"""Transcription d'une phrase : Groq Whisper (en ligne), puis Vosk (local, facultatif).

La clé Groq est lue dans la variable d'environnement GROQ_API_KEY, jamais dans un fichier
versionné (l'ancien dépôt a déjà dû retirer des secrets commités).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid


class SttError(RuntimeError):
    pass


class GroqWhisper:
    URL = 'https://api.groq.com/openai/v1/audio/transcriptions'

    def __init__(self, model: str = 'whisper-large-v3-turbo', language: str = 'fr',
                 timeout_s: float = 6.0, api_key: str | None = None) -> None:
        self.model, self.language, self.timeout = model, language, timeout_s
        self.key = api_key if api_key is not None else os.environ.get('GROQ_API_KEY', '')
        self.id = f'groq:{model}'

    @staticmethod
    def multipart(fields: dict[str, str], wav: bytes) -> tuple[bytes, str]:
        b = uuid.uuid4().hex
        parts = []
        for k, v in fields.items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="q.wav"\r\n'
                     'Content-Type: audio/wav\r\n\r\n'.encode() + wav + b'\r\n')
        parts.append(f'--{b}--\r\n'.encode())
        return b''.join(parts), f'multipart/form-data; boundary={b}'

    def transcribe(self, wav: bytes) -> str:
        if not self.key:
            raise SttError('GROQ_API_KEY absente')
        body, ctype = self.multipart({'model': self.model, 'language': self.language,
                                      'response_format': 'json', 'temperature': '0'}, wav)
        req = urllib.request.Request(self.URL, data=body, headers={
            'Content-Type': ctype, 'Authorization': f'Bearer {self.key}'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return str(json.loads(r.read().decode('utf-8')).get('text', '')).strip()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise SttError(f'Groq : {exc}') from exc


class VoskStt:
    """Reconnaissance locale, plus pauvre mais sans réseau (modèle vosk-model-small-fr)."""

    def __init__(self, model_dir: str, sample_rate: int = 16000) -> None:
        try:
            import vosk
        except ImportError as exc:
            raise SttError('vosk non installé (pip install vosk)') from exc
        if not os.path.isdir(os.path.expanduser(model_dir)):
            raise SttError(f'modèle Vosk introuvable : {model_dir}')
        vosk.SetLogLevel(-1)
        self._model = vosk.Model(os.path.expanduser(model_dir))
        self._rate = sample_rate
        self.id = 'vosk'

    def transcribe(self, wav: bytes) -> str:
        import vosk
        rec = vosk.KaldiRecognizer(self._model, self._rate)
        rec.AcceptWaveform(wav[44:])            # en-tête WAV de 44 octets retiré
        return str(json.loads(rec.FinalResult()).get('text', '')).strip()


def transcribe_first(engines: list, wav: bytes) -> tuple[str, str]:
    """Premier moteur qui réussit : (texte, moteur). Lève SttError si tous échouent."""
    errors = []
    for e in engines:
        try:
            return e.transcribe(wav), e.id
        except SttError as exc:
            errors.append(str(exc))
    raise SttError(' ; '.join(errors) or 'aucun moteur de transcription')
