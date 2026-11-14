import pytest

from musia_base import protocol as p
from musia_base.kinematics import DriveParams, twist_to_pwm


def test_frame_checksum_roundtrip():
    body = 'T 1234 120 -80 0'
    line = p.frame(body)
    assert line.endswith(b'\n')
    t = p.parse(line)
    assert t == p.Telemetry(1234, 120, -80, False)


def test_known_checksum():
    # 'S' = 0x53 : une seule lettre, la somme est son code
    assert p.stop() == b'S*53\n'
    assert p.ping() == b'P*50\n'


def test_motors_are_clamped():
    assert p.motors(400, -999).startswith(b'M 255 -255*')


def test_parse_accepts_crlf_and_lowercase_checksum():
    # l'Arduino termine par "\r\n" (Serial.println)
    body, cs = p.frame('H musia_base 2.0').decode().strip().split('*')
    assert p.parse(f'{body}*{cs.lower()}\r\n') == p.Hello('musia_base', '2.0')


@pytest.mark.parametrize('bad', [b'T 1 2 3 0', b'T 1 2 3 0*00\n', b'X*58\n', b'T a b c d*' + p.checksum('T a b c d').encode()])
def test_parse_rejects(bad):
    with pytest.raises(p.ProtocolError):
        p.parse(bad)


def test_watchdog_flag():
    assert p.parse(p.frame('T 5 0 0 1')).watchdog_tripped is True


def test_kinematics_straight_and_stop():
    d = DriveParams()
    assert twist_to_pwm(0.0, 0.0, d) == (0, 0)
    left, right = twist_to_pwm(0.25, 0.0, d)
    assert left == right > d.deadband_pwm


def test_kinematics_turn_in_place_is_symmetric():
    left, right = twist_to_pwm(0.0, 1.0, DriveParams())
    assert left == -right and right > 0          # rotation à gauche : roue droite en avant


def test_kinematics_saturation_keeps_ratio():
    d = DriveParams()
    left, right = twist_to_pwm(1.0, 2.0, d)      # bien au-delà des limites
    assert max(abs(left), abs(right)) == d.max_pwm
    assert 0 < left < right
