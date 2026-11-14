"""Nœuds ROS 2 du matériel branché sur le Pi : tête (servo), IMU, batterie.

head_node
  /musia/head/target  std_msgs/Float32   angle voulu en degrés (+ = à gauche)
  /musia/person       musia_interfaces/PersonDetection  si follow_person : regarde le visiteur
  -> /joint_states    sensor_msgs/JointState  articulation « tete_pan » (pour TF / URDF)
imu_node
  -> /imu/data_raw    sensor_msgs/Imu  (sans orientation : le MPU-6050 n'a pas de magnétomètre)
battery_node
  -> /musia/battery   sensor_msgs/BatteryState
"""

from __future__ import annotations

import math

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu, JointState
from std_msgs.msg import Float32

from musia_interfaces.msg import PersonDetection

from . import drivers as d


def _bus(n: int):
    from smbus2 import SMBus       # importé ici : seulement sur le robot
    return SMBus(n)


class HeadNode(Node):
    def __init__(self) -> None:
        super().__init__('head_node')
        p = d.ServoParams()
        for name, val in (('pwm_chip', 0), ('pwm_channel', 0), ('min_deg', p.min_deg),
                          ('max_deg', p.max_deg), ('center_us', p.center_us), ('us_per_deg', p.us_per_deg),
                          ('max_speed_dps', 90.0), ('follow_person', True), ('rate_hz', 50.0)):
            self.declare_parameter(name, val)
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self._p = d.ServoParams(float(g('min_deg')), float(g('max_deg')), float(g('center_us')),
                                float(g('us_per_deg')))
        self._dt = 1.0 / float(g('rate_hz'))
        self._step = float(g('max_speed_dps')) * self._dt
        self._angle = self._target = 0.0
        self._pwm = d.SysfsPwm(int(g('pwm_chip')), int(g('pwm_channel')))
        self._pwm.setup(self._p.period_ns)
        self._pwm.duty(d.angle_to_pulse_ns(0.0, self._p))
        self._pwm.enable(True)
        self.create_subscription(Float32, '/musia/head/target', lambda m: self._set(m.data), 10)
        if g('follow_person'):
            self.create_subscription(PersonDetection, '/musia/person', self._on_person, 10)
        self._pub = self.create_publisher(JointState, '/joint_states', 10)
        self.create_timer(self._dt, self._update)

    def _set(self, deg: float) -> None:
        self._target = max(self._p.min_deg, min(self._p.max_deg, deg))

    def _on_person(self, m: PersonDetection) -> None:
        # bearing est dans base_link : on vise directement cet angle
        if m.detected and not math.isnan(m.bearing_rad):
            self._set(math.degrees(m.bearing_rad))

    def _update(self) -> None:
        self._angle = d.step_towards(self._angle, self._target, self._step)
        self._pwm.duty(d.angle_to_pulse_ns(self._angle, self._p))
        js = JointState()
        js.header.stamp = self.get_clock().now().to_msg()
        js.name, js.position = ['tete_pan'], [math.radians(self._angle)]
        self._pub.publish(js)

    def destroy_node(self) -> None:
        try:
            self._pwm.enable(False)
        except OSError:
            pass
        super().destroy_node()


class ImuNode(Node):
    def __init__(self) -> None:
        super().__init__('imu_node')
        self.declare_parameter('i2c_bus', 1)
        self.declare_parameter('address', 0x68)
        self.declare_parameter('rate_hz', 50.0)
        self.declare_parameter('frame_id', 'imu_link')
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self._imu = d.Mpu6050(_bus(int(g('i2c_bus'))), int(g('address')))
        self._imu.start()
        self.get_logger().info('calibrage du gyroscope : ne pas bouger le robot…')
        bias = self._imu.calibrate()
        self.get_logger().info(f'biais du gyroscope (rad/s) : {[round(b, 4) for b in bias]}')
        self._frame = str(g('frame_id'))
        self._pub = self.create_publisher(Imu, '/imu/data_raw', 10)
        self.create_timer(1.0 / float(g('rate_hz')), self._read)

    def _read(self) -> None:
        try:
            s = self._imu.read()
        except OSError as exc:
            self.get_logger().warn(f'lecture IMU impossible : {exc}', throttle_duration_sec=5.0)
            return
        m = Imu()
        m.header.stamp = self.get_clock().now().to_msg()
        m.header.frame_id = self._frame
        m.orientation_covariance[0] = -1.0
        m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z = s.accel
        m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z = s.gyro
        for i in (0, 4, 8):
            m.linear_acceleration_covariance[i] = 0.04
            m.angular_velocity_covariance[i] = 0.0004
        self._pub.publish(m)


class BatteryNode(Node):
    def __init__(self) -> None:
        super().__init__('battery_node')
        self.declare_parameter('i2c_bus', 1)
        self.declare_parameter('address', 0x40)
        self.declare_parameter('shunt_ohm', 0.1)
        self.declare_parameter('empty_v', 6.4)      # pack 2 × 18650 (moteurs)
        self.declare_parameter('full_v', 8.4)
        self.declare_parameter('low_fraction', 0.2)
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self._ina = d.Ina219(_bus(int(g('i2c_bus'))), int(g('address')), float(g('shunt_ohm')))
        self._ina.start()
        self._empty, self._full, self._low = float(g('empty_v')), float(g('full_v')), float(g('low_fraction'))
        self._warned = False
        self._pub = self.create_publisher(BatteryState, '/musia/battery', 10)
        self.create_timer(1.0, self._read)

    def _read(self) -> None:
        try:
            v, i = self._ina.voltage_v(), self._ina.current_a()
        except OSError as exc:
            self.get_logger().warn(f'lecture INA219 impossible : {exc}', throttle_duration_sec=10.0)
            return
        frac = d.battery_fraction(v, self._empty, self._full)
        m = BatteryState()
        m.header.stamp = self.get_clock().now().to_msg()
        m.voltage, m.current, m.percentage = v, -i, frac
        m.power_supply_technology = BatteryState.POWER_SUPPLY_TECHNOLOGY_LION
        m.present = v > 1.0
        self._pub.publish(m)
        if frac < self._low and not self._warned:
            self.get_logger().warn(f'batterie faible : {v:.2f} V ({frac:.0%})')
        self._warned = frac < self._low


def _spin(cls) -> None:
    rclpy.init()
    node = cls()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


def head_main() -> None:
    _spin(HeadNode)


def imu_main() -> None:
    _spin(ImuNode)


def battery_main() -> None:
    _spin(BatteryNode)
