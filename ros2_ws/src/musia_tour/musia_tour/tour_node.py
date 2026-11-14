"""tour_node — exécute la machine à états de visite (engine.py) avec ROS 2.

Entrées
  /musia/person            musia_interfaces/PersonDetection   visiteur vu (webcam)
  /musia/question          std_msgs/String                    question transcrite (voix)
  service /musia/tour/command  musia_interfaces/TourCommand   start | next | home | stop
Sorties
  action  navigate_to_pose (Nav2)        déplacements
  action  /musia/say (musia_interfaces/Say)  paroles ; si aucun nœud de voix ne répond,
          le texte est écrit dans le journal et la fin est simulée (visite testable sans voix)
  /musia/led               std_msgs/String
  /musia/tour/status       musia_interfaces/TourStatus
"""

from __future__ import annotations

import math
import queue
import threading

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from std_msgs.msg import String

from musia_interfaces.action import Say as SayAction
from musia_interfaces.msg import PersonDetection, TourStatus
from musia_interfaces.srv import TourCommand

from . import engine as e
from .answers import Answerer
from .tour_file import load_file

CHARS_PER_S = 14.0          # débit de parole simulé quand il n'y a pas de voix


class TourNode(Node):
    def __init__(self) -> None:
        super().__init__('tour_node')
        self.declare_parameter('tour_file', '')
        self.declare_parameter('conversation_url', '')
        self.declare_parameter('person_max_distance_m', 1.8)
        self.declare_parameter('person_min_confidence', 0.5)
        self.declare_parameter('listen_s', 12.0)
        self.declare_parameter('answer_timeout_s', 8.0)
        self.declare_parameter('nav_retries', 1)
        self.declare_parameter('cooldown_s', 20.0)
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        tour = load_file(g('tour_file'))
        cfg = e.Config(listen_s=float(g('listen_s')), answer_timeout_s=float(g('answer_timeout_s')),
                       nav_retries=int(g('nav_retries')), cooldown_s=float(g('cooldown_s')))
        self._engine = e.TourEngine(tour, cfg)
        self._answerer = Answerer(tour, str(g('conversation_url')), timeout_s=cfg.answer_timeout_s - 1)
        self._max_dist, self._min_conf = float(g('person_max_distance_m')), float(g('person_min_confidence'))
        self._lock = threading.RLock()
        self._answers: queue.SimpleQueue = queue.SimpleQueue()
        self._nav_handles: dict[int, object] = {}
        self._say_handles: dict[int, object] = {}

        self._nav = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self._say = ActionClient(self, SayAction, '/musia/say')
        self._pub_led = self.create_publisher(String, '/musia/led', 10)
        self._pub_status = self.create_publisher(TourStatus, '/musia/tour/status', 10)
        self.create_subscription(PersonDetection, '/musia/person', self._on_person, 10)
        self.create_subscription(String, '/musia/question', self._on_question, 10)
        self.create_service(TourCommand, '/musia/tour/command', self._on_command)
        self.create_timer(0.1, self._on_tick)
        self.create_timer(0.5, self._publish_status)
        self.get_logger().info(f'visite « {tour.name} » : {len(tour.stops)} arrêts, '
                               f"conversation {'en ligne' if self._answerer.url else 'locale seulement'}")

    # --- entrées -----------------------------------------------------------------------
    def _now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def _feed(self, fn, *args) -> None:
        with self._lock:
            before = self._engine.phase
            actions = fn(*args, self._now())
            if self._engine.phase is not before:
                self.get_logger().info(f'{before.value} -> {self._engine.phase.value}')
                self._publish_status()
            for a in actions:
                self._run(a)

    def _on_person(self, m: PersonDetection) -> None:
        near = math.isnan(m.distance_m) or m.distance_m <= self._max_dist
        if m.detected and near and m.confidence >= self._min_conf:
            self._feed(self._engine.person_seen)

    def _on_question(self, m: String) -> None:
        self.get_logger().info(f'question : {m.data!r}')
        self._feed(self._engine.question, m.data)

    def _on_tick(self) -> None:
        # les réponses arrivent d'un fil séparé : on les rejoue ici, dans le fil ROS
        while not self._answers.empty():
            token, text = self._answers.get()
            self._feed(self._engine.answer, token, text)
        self._feed(self._engine.tick)

    def _on_command(self, req, res):
        with self._lock:
            res.accepted, res.message, actions = self._engine.command(req.command, self._now())
            for a in actions:
                self._run(a)
        self.get_logger().info(f'commande {req.command!r} : {res.message}')
        self._publish_status()
        return res

    # --- actions -----------------------------------------------------------------------
    def _run(self, a: e.Action) -> None:
        if isinstance(a, e.Say):
            self._speak(a)
        elif isinstance(a, e.GoTo):
            self._go(a)
        elif isinstance(a, e.Ask):
            threading.Thread(target=self._ask, args=(a,), daemon=True).start()
        elif isinstance(a, e.Led):
            self._pub_led.publish(String(data=a.pattern))
        elif isinstance(a, e.CancelNav):
            for h in list(self._nav_handles.values()):
                h.cancel_goal_async()
            self._nav_handles.clear()
        elif isinstance(a, e.StopSpeech):
            for h in list(self._say_handles.values()):
                h.cancel_goal_async()
            self._say_handles.clear()

    def _speak(self, a: e.Say) -> None:
        if not self._say.server_is_ready():
            self.get_logger().info(f'[voix absente] « {a.text} »')
            delay = max(1.0, len(a.text) / CHARS_PER_S)
            timer = None

            def done():
                self.destroy_timer(timer)
                self._feed(self._engine.speech_done, a.token)
            timer = self.create_timer(delay, done)
            return
        goal = SayAction.Goal(text=a.text, cache_key='')
        fut = self._say.send_goal_async(goal)
        fut.add_done_callback(lambda f: self._on_say_accepted(a.token, f))

    def _on_say_accepted(self, token: int, fut) -> None:
        h = fut.result()
        if not h.accepted:
            self._feed(self._engine.speech_done, token)
            return
        self._say_handles[token] = h

        def finished(_f):
            self._say_handles.pop(token, None)
            self._feed(self._engine.speech_done, token)
        h.get_result_async().add_done_callback(finished)

    def _go(self, a: e.GoTo) -> None:
        if not self._nav.server_is_ready():           # non bloquant : on est dans le fil ROS
            self.get_logger().error('Nav2 ne répond pas (navigate_to_pose)')
            self._feed(self._engine.nav_done, a.token, False)
            return
        p = PoseStamped()
        p.header.frame_id = 'map'
        p.header.stamp = self.get_clock().now().to_msg()
        p.pose.position.x, p.pose.position.y = a.pose.x, a.pose.y
        p.pose.orientation.z, p.pose.orientation.w = math.sin(a.pose.yaw / 2), math.cos(a.pose.yaw / 2)
        fut = self._nav.send_goal_async(NavigateToPose.Goal(pose=p))
        fut.add_done_callback(lambda f: self._on_nav_accepted(a.token, f))

    def _on_nav_accepted(self, token: int, fut) -> None:
        h = fut.result()
        if not h.accepted:
            self._feed(self._engine.nav_done, token, False)
            return
        self._nav_handles[token] = h

        def finished(f):
            self._nav_handles.pop(token, None)
            status = f.result().status
            if status != GoalStatus.STATUS_CANCELED:     # une annulation vient de nous : rien à dire
                self._feed(self._engine.nav_done, token, status == GoalStatus.STATUS_SUCCEEDED)
        h.get_result_async().add_done_callback(finished)

    def _ask(self, a: e.Ask) -> None:
        text, source = self._answerer.answer(a.question, a.stop_id)
        self.get_logger().info(f'réponse ({source}) : {text!r}')
        self._answers.put((a.token, text))

    def _publish_status(self) -> None:
        m = TourStatus()
        m.header.stamp = self.get_clock().now().to_msg()
        m.phase = self._engine.phase.value
        stop = self._engine.stop
        m.stop_id = stop.id if stop else ''
        m.stop_index = self._engine.index
        m.stop_count = len(self._engine.tour.stops)
        m.online = self._answerer.online
        self._pub_status.publish(m)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = TourNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
