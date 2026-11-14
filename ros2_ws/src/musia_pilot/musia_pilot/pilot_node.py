"""Nœud de pilotage : relie la page web (web.py) aux topics ROS.

    page ──HTTP──▶ pilot_node ──▶ /cmd_vel_teleop (Twist)       pilotage manuel
                              ──▶ /goal_pose (PoseStamped)       destination → Nav2
                              ──▶ navigate_to_pose cancel_goal   annulation
                              ──▶ /musia/estop (Bool)            verrou de twist_mux
                              ──▶ /musia/head/target (Float32)   tête
                              ──▶ /musia/nav/state (String)      repos, en_route, arrive…
                 ◀── TF map→base_footprint, /map, statut Nav2, /musia/battery, /musia/base/status

Toucher le joystick pendant une navigation annule la destination : on reprend la main,
le robot ne repart pas seul quand on relâche.
"""

import math
import os
import threading

import rclpy
from action_msgs.msg import GoalStatusArray
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped, Twist
from nav_msgs.msg import OccupancyGrid
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rclpy.time import Time
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Bool, Float32, String
from tf2_ros import Buffer, TransformException, TransformListener

from musia_interfaces.msg import BaseStatus

from .mapimage import occupancy_png, quaternion_from_yaw, yaw_from_quaternion
from .navstatus import ACTIVE, summarize
from .points import PointsStore
from .web import PilotServer

_LATCHED = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL)


class RosRobot(Node):
    def __init__(self) -> None:
        super().__init__('pilot_node')
        p = self.declare_parameter
        self.port = p('port', 8080).value
        self.camera_port = p('camera_port', 8081).value
        self.max_linear = p('max_linear_mps', 0.25).value
        self.max_angular = p('max_angular_rps', 1.5).value
        self.points_file = os.path.expanduser(p('points_file', '~/.config/musia/points.json').value)
        self.static_dir = p('static_dir', '').value
        self.map_frame = p('map_frame', 'map').value
        self.base_frame = p('base_frame', 'base_footprint').value

        self._lock = threading.Lock()
        self._pose = None
        self._nav = 'repos'
        self._estop = False
        self._battery = None
        self._base = None
        self._map = None          # dernier OccupancyGrid
        self._map_version = 0
        self._png = (None, -1)    # (image, version encodée)

        self._teleop = self.create_publisher(Twist, '/cmd_vel_teleop', 10)
        self._goal = self.create_publisher(PoseStamped, '/goal_pose', 10)
        self._estop_pub = self.create_publisher(Bool, '/musia/estop', 10)
        self._head = self.create_publisher(Float32, '/musia/head/target', 10)
        self._nav_pub = self.create_publisher(String, '/musia/nav/state', 10)
        self._cancel = self.create_client(CancelGoal, '/navigate_to_pose/_action/cancel_goal')

        self.create_subscription(OccupancyGrid, '/map', self._on_map, _LATCHED)
        self.create_subscription(GoalStatusArray, '/navigate_to_pose/_action/status',
                                 self._on_status, _LATCHED)
        self.create_subscription(BatteryState, '/musia/battery', self._on_battery, 10)
        self.create_subscription(BaseStatus, '/musia/base/status', self._on_base, 10)

        self._tf = Buffer()
        self._tf_listener = TransformListener(self._tf, self)
        self.create_timer(0.2, self._update_pose)
        # le verrou de twist_mux garde le dernier état reçu : on le republie pour qu'un
        # twist_mux redémarré ne reste pas sans consigne
        self.create_timer(1.0, self._publish_estop)

    # --- entrées ROS ------------------------------------------------------------------
    def _on_map(self, msg: OccupancyGrid) -> None:
        with self._lock:
            self._map = msg
            self._map_version += 1

    def _on_status(self, msg: GoalStatusArray) -> None:
        goals = [(g.goal_info.stamp.sec * 10**9 + g.goal_info.stamp.nanosec, g.status)
                 for g in msg.status_list]
        state = summarize(goals)
        with self._lock:
            self._nav = state
        self._nav_pub.publish(String(data=state))

    def _on_battery(self, msg: BatteryState) -> None:
        pct = msg.percentage
        with self._lock:
            self._battery = {'voltage': round(msg.voltage, 2),
                             'percent': None if math.isnan(pct) else round(pct * 100)}

    def _on_base(self, msg: BaseStatus) -> None:
        with self._lock:
            self._base = {'connected': msg.connected, 'watchdog': msg.watchdog_tripped,
                          'firmware': msg.firmware}

    def _update_pose(self) -> None:
        try:
            t = self._tf.lookup_transform(self.map_frame, self.base_frame, Time())
        except TransformException:
            pose = None   # pas encore de carte, ou localisation perdue
        else:
            q = t.transform.rotation
            pose = (t.transform.translation.x, t.transform.translation.y,
                    yaw_from_quaternion(q.x, q.y, q.z, q.w))
        with self._lock:
            self._pose = pose

    def _publish_estop(self) -> None:
        self._estop_pub.publish(Bool(data=self._estop))

    # --- interface utilisée par web.py (appelée depuis les fils du serveur HTTP) --------
    def current_pose(self):
        with self._lock:
            return self._pose

    def state(self) -> dict:
        with self._lock:
            m = self._map
            info = None if m is None else {
                'version': self._map_version, 'width': m.info.width, 'height': m.info.height,
                'resolution': m.info.resolution,
                'origin_x': m.info.origin.position.x, 'origin_y': m.info.origin.position.y}
            pose = None if self._pose is None else dict(zip(('x', 'y', 'yaw'), self._pose))
            return {'pose': pose, 'nav': self._nav, 'estop': self._estop,
                    'battery': self._battery, 'base': self._base, 'map': info,
                    'camera_port': self.camera_port}

    def map_png(self):
        with self._lock:
            m, version = self._map, self._map_version
            png, done = self._png
        if m is None:
            return None
        if done != version:
            data = m.data.tobytes() if hasattr(m.data, 'tobytes') else bytes(v & 0xFF for v in m.data)
            png = occupancy_png(m.info.width, m.info.height, data)
            with self._lock:
                self._png = (png, version)
        return png

    def drive(self, linear: float, angular: float) -> None:
        if self._estop:
            return
        with self._lock:
            navigating = self._nav in ACTIVE
        if navigating and (abs(linear) > 1e-3 or abs(angular) > 1e-3):
            self.cancel()
        msg = Twist()
        msg.linear.x, msg.angular.z = linear, angular
        self._teleop.publish(msg)

    def goal(self, x: float, y: float, yaw: float) -> None:
        msg = PoseStamped()
        msg.header.frame_id = self.map_frame
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.position.x, msg.pose.position.y = x, y
        q = msg.pose.orientation
        q.x, q.y, q.z, q.w = quaternion_from_yaw(yaw)
        self._goal.publish(msg)
        self.get_logger().info(f'destination x={x:.2f} y={y:.2f} cap={math.degrees(yaw):.0f}°')

    def cancel(self) -> None:
        if not self._cancel.service_is_ready():
            self.get_logger().warning('annulation impossible : Nav2 ne répond pas')
            return
        self._cancel.call_async(CancelGoal.Request())   # requête vide : tous les objectifs

    def set_estop(self, on: bool) -> None:
        self._estop = on
        self._publish_estop()
        if on:
            self.cancel()
            self._teleop.publish(Twist())
            self.get_logger().warning('ARRÊT D\'URGENCE')
        else:
            self.get_logger().info('arrêt d\'urgence relâché')

    def head(self, deg: float) -> None:
        self._head.publish(Float32(data=float(deg)))


def main() -> None:
    rclpy.init()
    robot = RosRobot()
    static = robot.static_dir
    if not static:
        from ament_index_python.packages import get_package_share_directory
        static = os.path.join(get_package_share_directory('musia_pilot'), 'static')
    server = PilotServer(('0.0.0.0', robot.port), robot, PointsStore(robot.points_file), static,
                         max_linear=robot.max_linear, max_angular=robot.max_angular)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    robot.get_logger().info(f'page de pilotage : http://<adresse-du-robot>:{robot.port}/')
    try:
        rclpy.spin(robot)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        robot.destroy_node()
        rclpy.try_shutdown()
