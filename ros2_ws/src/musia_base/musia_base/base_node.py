"""base_node — pont entre /cmd_vel et la carte moteur Arduino (firmware musia_base).

Souscrit : /cmd_vel (geometry_msgs/Twist)
Publie   : /musia/base/status (musia_interfaces/BaseStatus), 10 Hz

La consigne est renvoyée à 20 Hz pour entretenir le chien de garde de l'Arduino ;
si /cmd_vel se tait plus de cmd_timeout_s, la consigne envoyée retombe à zéro.
"""

from __future__ import annotations

import threading
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node

from musia_interfaces.msg import BaseStatus

from . import protocol
from .kinematics import DriveParams, twist_to_pwm

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit('pyserial manquant : sudo apt install python3-serial') from exc


class BaseNode(Node):
    def __init__(self) -> None:
        super().__init__('base_node')
        d = DriveParams()
        self.declare_parameter('serial_port', '/dev/serial/by-id/CHANGER-arduino')
        self.declare_parameter('baud_rate', 115200)
        self.declare_parameter('cmd_timeout_s', 0.5)
        self.declare_parameter('send_rate_hz', 20.0)
        self.declare_parameter('track_m', d.track_m)
        self.declare_parameter('slip_factor', d.slip_factor)
        self.declare_parameter('max_wheel_speed_mps', d.max_wheel_speed_mps)
        self.declare_parameter('deadband_pwm', d.deadband_pwm)

        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self._port, self._baud = g('serial_port'), int(g('baud_rate'))
        self._cmd_timeout = float(g('cmd_timeout_s'))
        self._drive = DriveParams(track_m=float(g('track_m')), slip_factor=float(g('slip_factor')),
                                  max_wheel_speed_mps=float(g('max_wheel_speed_mps')),
                                  deadband_pwm=int(g('deadband_pwm')))

        self._lock = threading.Lock()
        self._serial: serial.Serial | None = None
        self._pwm = (0, 0)
        self._last_cmd = 0.0
        self._last_rx = 0.0
        self._telemetry: protocol.Telemetry | None = None
        self._firmware = ''
        self._running = True

        self.create_subscription(Twist, '/cmd_vel', self._on_cmd_vel, 10)
        self._pub = self.create_publisher(BaseStatus, '/musia/base/status', 10)
        self.create_timer(1.0 / float(g('send_rate_hz')), self._send)
        self.create_timer(0.1, self._publish_status)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.get_logger().info(f'base_node : port {self._port} à {self._baud} bauds')

    # --- consigne ---------------------------------------------------------------
    def _on_cmd_vel(self, msg: Twist) -> None:
        self._pwm = twist_to_pwm(msg.linear.x, msg.angular.z, self._drive)
        self._last_cmd = time.monotonic()

    def _send(self) -> None:
        fresh = time.monotonic() - self._last_cmd < self._cmd_timeout
        left, right = self._pwm if fresh else (0, 0)
        self._write(protocol.motors(left, right))

    def _write(self, data: bytes) -> None:
        with self._lock:
            if self._serial is None:
                return
            try:
                self._serial.write(data)
            except serial.SerialException as exc:
                self.get_logger().warn(f'écriture série impossible : {exc}')
                self._close()

    # --- lecture ------------------------------------------------------------------
    def _open(self) -> bool:
        try:
            s = serial.Serial(self._port, self._baud, timeout=0.5)
        except serial.SerialException as exc:
            self.get_logger().error(f'ouverture de {self._port} impossible : {exc}',
                                    throttle_duration_sec=10.0)
            return False
        time.sleep(2.0)                     # l'UNO redémarre à l'ouverture du port
        s.reset_input_buffer()
        with self._lock:
            self._serial = s
        self._write(protocol.ping())
        return True

    def _close(self) -> None:
        # appelé avec ou sans le verrou : on ne fait que fermer
        s, self._serial = self._serial, None
        if s is not None:
            try:
                s.close()
            except serial.SerialException:
                pass

    def _read_loop(self) -> None:
        while self._running:
            if self._serial is None and not self._open():
                time.sleep(3.0)
                continue
            try:
                raw = self._serial.readline() if self._serial else b''
            except serial.SerialException as exc:
                self.get_logger().warn(f'liaison série perdue : {exc}')
                with self._lock:
                    self._close()
                continue
            if raw:
                self._handle(raw)

    def _handle(self, raw: bytes) -> None:
        try:
            f = protocol.parse(raw)
        except protocol.ProtocolError as exc:
            self.get_logger().debug(str(exc))
            return
        self._last_rx = time.monotonic()
        if isinstance(f, protocol.Telemetry):
            if f.watchdog_tripped and (self._telemetry is None or not self._telemetry.watchdog_tripped):
                self.get_logger().info('chien de garde Arduino : moteurs coupés')
            self._telemetry = f
        elif isinstance(f, protocol.Hello):
            self._firmware = f'{f.name} {f.version}'
            self.get_logger().info(f'carte moteur prête : {self._firmware}')
        elif isinstance(f, protocol.Error):
            self.get_logger().warn(f"l'Arduino signale l'erreur {f.code}")

    def _publish_status(self) -> None:
        m = BaseStatus()
        m.header.stamp = self.get_clock().now().to_msg()
        m.connected = time.monotonic() - self._last_rx < 1.0
        t = self._telemetry
        m.watchdog_tripped = bool(t.watchdog_tripped) if t else True
        m.pwm_left, m.pwm_right = (t.pwm_left, t.pwm_right) if t else (0, 0)
        m.firmware = self._firmware
        self._pub.publish(m)

    def destroy_node(self) -> None:
        self._running = False
        self._write(protocol.stop())
        with self._lock:
            self._close()
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = BaseNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
