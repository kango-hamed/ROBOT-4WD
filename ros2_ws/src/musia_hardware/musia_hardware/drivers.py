"""Pilotes du matériel branché sur le Raspberry Pi 5 — sans ROS, testables avec un faux bus.

  * servo 9 g (rotation de la webcam) : PWM matériel du Pi via sysfs ;
  * MPU-6050 (IMU, plaque à trous du module MUSIA) : I²C, adresse 0x68 ;
  * INA219 (courant et tension batterie, couche batterie) : I²C, adresse 0x40.

Le bus I²C est n'importe quel objet qui offre read_i2c_block_data / write_byte_data
(smbus2.SMBus sur le robot, un faux bus dans les tests).
"""

from __future__ import annotations

import math
import os
import struct
from dataclasses import dataclass

G = 9.80665


# --- Servo ------------------------------------------------------------------------------
@dataclass(frozen=True)
class ServoParams:
    min_deg: float = -80.0          # butées logicielles (le câble USB de la webcam suit)
    max_deg: float = 80.0
    center_us: float = 1500.0
    us_per_deg: float = 11.1        # SG90 : environ 500..2500 µs pour 180°, à ajuster
    period_ns: int = 20_000_000     # 50 Hz


def angle_to_pulse_ns(angle_deg: float, p: ServoParams) -> int:
    a = max(p.min_deg, min(p.max_deg, angle_deg))
    return int(round((p.center_us + a * p.us_per_deg) * 1000))


class SysfsPwm:
    """Canal PWM matériel (/sys/class/pwm/pwmchipN/pwmM). Sur le Pi 5, il faut une ligne
    dtoverlay=pwm… dans /boot/firmware/config.txt (voir le README, section Servo)."""

    def __init__(self, chip: int, channel: int, root: str = '/sys/class/pwm') -> None:
        self.base = os.path.join(root, f'pwmchip{chip}')
        self.dir = os.path.join(self.base, f'pwm{channel}')
        if not os.path.isdir(self.dir):
            self._write(os.path.join(self.base, 'export'), channel)

    @staticmethod
    def _write(path: str, value) -> None:
        with open(path, 'w') as f:
            f.write(str(value))

    def setup(self, period_ns: int) -> None:
        self._write(os.path.join(self.dir, 'period'), period_ns)

    def duty(self, ns: int) -> None:
        self._write(os.path.join(self.dir, 'duty_cycle'), ns)

    def enable(self, on: bool) -> None:
        self._write(os.path.join(self.dir, 'enable'), 1 if on else 0)


def step_towards(current: float, target: float, max_step: float) -> float:
    if abs(target - current) <= max_step:
        return target
    return current + math.copysign(max_step, target - current)


# --- MPU-6050 ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ImuSample:
    accel: tuple[float, float, float]   # m/s²
    gyro: tuple[float, float, float]    # rad/s
    temp_c: float


class Mpu6050:
    PWR_MGMT_1, CONFIG, GYRO_CONFIG, ACCEL_CONFIG, ACCEL_XOUT_H, WHO_AM_I = 0x6B, 0x1A, 0x1B, 0x1C, 0x3B, 0x75
    ACCEL_LSB_PER_G = 16384.0           # ±2 g
    GYRO_LSB_PER_DPS = 131.0            # ±250 °/s

    def __init__(self, bus, address: int = 0x68) -> None:
        self.bus, self.addr = bus, address
        self.gyro_bias = (0.0, 0.0, 0.0)

    def start(self) -> None:
        who = self.bus.read_i2c_block_data(self.addr, self.WHO_AM_I, 1)[0]
        if who not in (0x68, 0x70, 0x72):   # 0x70/0x72 : clones MPU-6500 vendus comme 6050
            raise OSError(f'MPU-6050 introuvable en 0x{self.addr:02X} (WHO_AM_I = 0x{who:02X})')
        self.bus.write_byte_data(self.addr, self.PWR_MGMT_1, 0x01)    # réveil, horloge du gyro X
        self.bus.write_byte_data(self.addr, self.CONFIG, 0x03)        # filtre passe-bas ~44 Hz
        self.bus.write_byte_data(self.addr, self.GYRO_CONFIG, 0x00)
        self.bus.write_byte_data(self.addr, self.ACCEL_CONFIG, 0x00)

    def read_raw(self) -> ImuSample:
        ax, ay, az, t, gx, gy, gz = struct.unpack('>7h', bytes(
            self.bus.read_i2c_block_data(self.addr, self.ACCEL_XOUT_H, 14)))
        k_a = G / self.ACCEL_LSB_PER_G
        k_g = math.radians(1.0 / self.GYRO_LSB_PER_DPS)
        return ImuSample((ax * k_a, ay * k_a, az * k_a), (gx * k_g, gy * k_g, gz * k_g), t / 340.0 + 36.53)

    def read(self) -> ImuSample:
        s = self.read_raw()
        return ImuSample(s.accel, tuple(g - b for g, b in zip(s.gyro, self.gyro_bias)), s.temp_c)

    def calibrate(self, samples: int = 200) -> tuple[float, float, float]:
        """Biais du gyroscope, robot immobile. À appeler au démarrage."""
        acc = [0.0, 0.0, 0.0]
        for _ in range(samples):
            g = self.read_raw().gyro
            acc = [a + v for a, v in zip(acc, g)]
        self.gyro_bias = tuple(a / samples for a in acc)
        return self.gyro_bias


# --- INA219 -----------------------------------------------------------------------------
class Ina219:
    CONFIG, SHUNT, BUS = 0x00, 0x01, 0x02

    def __init__(self, bus, address: int = 0x40, shunt_ohm: float = 0.1) -> None:
        self.bus, self.addr, self.shunt = bus, address, shunt_ohm

    def start(self) -> None:
        # BRNG=1 (32 V) · PG=11 (±320 mV) · BADC=SADC=1011 (12 bits, moyenne de 8) · mode continu
        cfg = (1 << 13) | (0b11 << 11) | (0b1011 << 7) | (0b1011 << 3) | 0b111
        self.bus.write_i2c_block_data(self.addr, self.CONFIG, [cfg >> 8, cfg & 0xFF])

    def _reg(self, reg: int) -> int:
        hi, lo = self.bus.read_i2c_block_data(self.addr, reg, 2)
        return (hi << 8) | lo

    def voltage_v(self) -> float:
        return (self._reg(self.BUS) >> 3) * 0.004

    def current_a(self) -> float:
        raw = self._reg(self.SHUNT)
        if raw & 0x8000:
            raw -= 1 << 16
        return raw * 10e-6 / self.shunt


def battery_fraction(voltage: float, empty_v: float, full_v: float) -> float:
    """Charge estimée par la tension (linéaire, grossier mais suffisant pour alerter)."""
    return max(0.0, min(1.0, (voltage - empty_v) / (full_v - empty_v)))
