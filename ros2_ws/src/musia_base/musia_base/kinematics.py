"""Conversion consigne de vitesse (Twist) -> PWM des deux côtés (4 roues, direction par glissement).

Les moteurs TT n'ont pas d'encodeur : la commande est en boucle ouverte. Les paramètres
(vitesse à PWM max, zone morte, facteur de glissement) sont à étalonner sur le robot
(voir le README, section Étalonnage).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DriveParams:
    track_m: float = 0.150            # voie, entre milieux de roues (squelette CAO : voie = 150)
    slip_factor: float = 1.5          # > 1 : il faut plus d'écart de vitesse pour tourner en 4 roues
    max_wheel_speed_mps: float = 0.50  # vitesse d'une roue à PWM 255 (à mesurer)
    deadband_pwm: int = 70            # en dessous, le moteur ne démarre pas (à mesurer)
    max_pwm: int = 255


def wheel_speeds(linear_mps: float, angular_rps: float, p: DriveParams) -> tuple[float, float]:
    half = p.track_m * p.slip_factor / 2.0
    return linear_mps - angular_rps * half, linear_mps + angular_rps * half


def speed_to_pwm(speed_mps: float, p: DriveParams) -> int:
    if abs(speed_mps) < 1e-3:
        return 0
    ratio = min(1.0, abs(speed_mps) / p.max_wheel_speed_mps)
    pwm = p.deadband_pwm + ratio * (p.max_pwm - p.deadband_pwm)
    return int(round(pwm if speed_mps > 0 else -pwm))


def twist_to_pwm(linear_mps: float, angular_rps: float, p: DriveParams) -> tuple[int, int]:
    """Renvoie (pwm_gauche, pwm_droite). Si une roue sature, les deux sont réduites
    dans la même proportion pour conserver la courbure demandée."""
    left, right = wheel_speeds(linear_mps, angular_rps, p)
    peak = max(abs(left), abs(right))
    if peak > p.max_wheel_speed_mps:
        k = p.max_wheel_speed_mps / peak
        left, right = left * k, right * k
    return speed_to_pwm(left, p), speed_to_pwm(right, p)
