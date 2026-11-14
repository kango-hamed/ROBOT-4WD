import math
import struct

import pytest

from musia_hardware import drivers as d


class FakeBus:
    def __init__(self, regs):
        self.regs = dict(regs)
        self.writes = []

    def read_i2c_block_data(self, addr, reg, n):
        return list(self.regs[(addr, reg)][:n])

    def write_byte_data(self, addr, reg, value):
        self.writes.append((addr, reg, [value]))

    def write_i2c_block_data(self, addr, reg, data):
        self.writes.append((addr, reg, list(data)))


def test_servo_pulse_and_limits():
    p = d.ServoParams()
    assert d.angle_to_pulse_ns(0, p) == 1_500_000
    assert d.angle_to_pulse_ns(500, p) == d.angle_to_pulse_ns(p.max_deg, p)
    assert d.angle_to_pulse_ns(-45, p) < 1_500_000


def test_step_towards():
    assert d.step_towards(0, 10, 3) == 3
    assert d.step_towards(9, 10, 3) == 10
    assert d.step_towards(0, -10, 3) == -3


def test_sysfs_pwm(tmp_path):
    chip = tmp_path / 'pwmchip0'
    (chip / 'pwm1').mkdir(parents=True)
    pwm = d.SysfsPwm(0, 1, root=str(tmp_path))
    pwm.setup(20_000_000)
    pwm.duty(1_500_000)
    pwm.enable(True)
    assert (chip / 'pwm1' / 'duty_cycle').read_text() == '1500000'
    assert (chip / 'pwm1' / 'enable').read_text() == '1'


def mpu_bus(ax=0, ay=0, az=16384, gx=0, gy=0, gz=131):
    data = struct.pack('>7h', ax, ay, az, 0, gx, gy, gz)
    return FakeBus({(0x68, 0x75): [0x68], (0x68, 0x3B): list(data)})


def test_mpu6050_scaling_and_bias():
    imu = d.Mpu6050(mpu_bus())
    imu.start()
    s = imu.read()
    assert s.accel[2] == pytest.approx(d.G)
    assert s.gyro[2] == pytest.approx(math.radians(1.0))       # 131 LSB = 1 °/s
    imu.calibrate(samples=5)
    assert imu.read().gyro[2] == pytest.approx(0.0)


def test_mpu6050_absent():
    with pytest.raises(OSError):
        d.Mpu6050(FakeBus({(0x68, 0x75): [0x00]})).start()


def test_ina219():
    # tension : 7,4 V -> 1850 pas de 4 mV, décalés de 3 bits ; shunt : -12,3 mV -> -1230 pas de 10 µV
    bus_raw = 1850 << 3
    shunt_raw = (-1230) & 0xFFFF
    bus = FakeBus({(0x40, 0x02): [bus_raw >> 8, bus_raw & 0xFF],
                   (0x40, 0x01): [shunt_raw >> 8, shunt_raw & 0xFF]})
    ina = d.Ina219(bus, shunt_ohm=0.1)
    ina.start()
    assert bus.writes[0] == (0x40, 0x00, [0x3D, 0xDF])      # valeur de la fiche technique
    assert ina.voltage_v() == pytest.approx(7.4)
    assert ina.current_a() == pytest.approx(-0.123)
    assert d.battery_fraction(7.4, 6.4, 8.4) == pytest.approx(0.5)
    assert d.battery_fraction(9.0, 6.4, 8.4) == 1.0
