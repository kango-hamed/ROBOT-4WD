"""Protocole série Raspberry Pi <-> Arduino (firmware/musia_base/musia_base.ino).

Une trame = une ligne ASCII terminée par '\\n', suivie d'une somme de contrôle :
    <corps>*<XX>      XX = OU exclusif de tous les octets du corps, en hexadécimal majuscule

Pi -> Arduino
    M <g> <d>         consigne PWM signée des côtés gauche et droit, -255..255
    S                 arrêt immédiat
    P                 ping (l'Arduino répond par une trame H)

Arduino -> Pi
    H <nom> <version>                 démarrage ou réponse au ping
    T <ms> <g> <d> <chien_de_garde>   télémétrie à 10 Hz : PWM réellement appliqués
                                      (après rampe), 1 si les moteurs sont coupés
                                      faute de consigne depuis 400 ms
    E <code>                          erreur : 1 = trame illisible, 2 = somme fausse,
                                      3 = commande inconnue

Module sans dépendance ROS : testable sur n'importe quel PC.
"""

from __future__ import annotations

from dataclasses import dataclass

PWM_MAX = 255


def checksum(body: str) -> str:
    x = 0
    for b in body.encode('ascii'):
        x ^= b
    return f'{x:02X}'


def frame(body: str) -> bytes:
    return f'{body}*{checksum(body)}\n'.encode('ascii')


def clamp_pwm(v: float) -> int:
    return max(-PWM_MAX, min(PWM_MAX, int(round(v))))


def motors(left: float, right: float) -> bytes:
    return frame(f'M {clamp_pwm(left)} {clamp_pwm(right)}')


def stop() -> bytes:
    return frame('S')


def ping() -> bytes:
    return frame('P')


@dataclass(frozen=True)
class Hello:
    name: str
    version: str


@dataclass(frozen=True)
class Telemetry:
    millis: int
    pwm_left: int
    pwm_right: int
    watchdog_tripped: bool


@dataclass(frozen=True)
class Error:
    code: int


class ProtocolError(ValueError):
    pass


def parse(line: bytes | str) -> Hello | Telemetry | Error:
    """Décode une trame de l'Arduino. Lève ProtocolError si elle est invalide."""
    if isinstance(line, bytes):
        line = line.decode('ascii', errors='replace')
    line = line.strip()
    body, sep, cs = line.rpartition('*')
    if not sep or not body:
        raise ProtocolError(f'trame sans somme de contrôle : {line!r}')
    if cs.upper() != checksum(body):
        raise ProtocolError(f'somme de contrôle fausse : {line!r}')
    parts = body.split()
    kind, args = parts[0], parts[1:]
    try:
        if kind == 'H' and len(args) == 2:
            return Hello(args[0], args[1])
        if kind == 'T' and len(args) == 4:
            return Telemetry(int(args[0]), int(args[1]), int(args[2]), args[3] == '1')
        if kind == 'E' and len(args) == 1:
            return Error(int(args[0]))
    except ValueError as exc:
        raise ProtocolError(f'champ numérique illisible : {line!r}') from exc
    raise ProtocolError(f'trame inconnue : {line!r}')
