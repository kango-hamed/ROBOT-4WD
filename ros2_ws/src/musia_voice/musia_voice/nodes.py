"""Nœuds de voix de MUSIA.

say_node
  action  /musia/say (musia_interfaces/Say)   une phrase à la fois, dans l'ordre d'arrivée ;
                                              l'annulation coupe la lecture immédiatement
  -> /musia/speaking  std_msgs/Bool           vrai pendant la lecture (coupe l'écoute)
listen_node
  -> /musia/question  std_msgs/String         phrase entendue et transcrite
  /musia/speaking     std_msgs/Bool           le robot n'écoute pas pendant qu'il parle
  /musia/listen       std_msgs/Bool           activer / couper l'écoute (vrai par défaut)
"""

from __future__ import annotations

import subprocess
import threading
import time

import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool, String

from musia_interfaces.action import Say

from . import listening as li
from . import speech as sp
from . import transcription as tr


class SayNode(Node):
    def __init__(self) -> None:
        super().__init__('say_node')
        self.declare_parameter('cache_dir', '~/.cache/musia/voix')
        self.declare_parameter('edge_voice', 'fr-FR-DeniseNeural')
        self.declare_parameter('edge_rate', '-5%')
        self.declare_parameter('piper_model', '~/musia/voix/fr_FR-siwis-medium.onnx')
        self.declare_parameter('audio_device', 'default')
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        engines = [sp.EdgeTts(str(g('edge_voice')), str(g('edge_rate'))), sp.PiperTts(str(g('piper_model')))]
        self._voice = sp.Voice(engines, sp.SpeechCache(str(g('cache_dir'))))
        self._device = str(g('audio_device'))
        self._pub = self.create_publisher(Bool, '/musia/speaking', 10)
        self._serial = threading.Lock()         # une seule phrase à la fois
        ActionServer(self, Say, '/musia/say', execute_callback=self._execute,
                     goal_callback=lambda _g: GoalResponse.ACCEPT,
                     cancel_callback=lambda _h: CancelResponse.ACCEPT,
                     callback_group=ReentrantCallbackGroup())
        self.get_logger().info('voix prête : Edge TTS, puis Piper hors ligne')

    def _execute(self, handle):
        text = handle.request.text.strip()
        result = Say.Result()
        with self._serial:
            if handle.is_cancel_requested or not text:
                handle.canceled() if handle.is_cancel_requested else handle.succeed()
                return result
            try:
                path, origin = self._voice.render(text)
            except sp.TtsError as exc:
                self.get_logger().error(f'synthèse impossible : {exc}')
                handle.abort()
                return result
            self.get_logger().info(f'« {text[:60]} » ({origin})')
            t0 = time.monotonic()
            self._pub.publish(Bool(data=True))
            try:
                proc = subprocess.Popen(sp.player_command(path, self._device),
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            except OSError as exc:
                self._pub.publish(Bool(data=False))
                self.get_logger().error(f'lecteur audio absent : {exc}')
                handle.abort()
                return result
            while proc.poll() is None:
                if handle.is_cancel_requested:
                    proc.terminate()
                    proc.wait(timeout=2)
                    self._pub.publish(Bool(data=False))
                    handle.canceled()
                    return result
                time.sleep(0.05)
            self._pub.publish(Bool(data=False))
            result.duration_s = time.monotonic() - t0
            if proc.returncode != 0:
                err = proc.stderr.read().decode(errors='replace').strip() if proc.stderr else ''
                self.get_logger().error(f'lecture échouée ({proc.returncode}) : {err}')
                handle.abort()
                return result
            result.success = True
            handle.succeed()
            return result


class ListenNode(Node):
    def __init__(self) -> None:
        super().__init__('listen_node')
        self.declare_parameter('audio_device', 'default')     # périphérique ALSA du micro I²S
        self.declare_parameter('vad', 'webrtc')               # webrtc | energie
        self.declare_parameter('webrtc_aggressiveness', 2)
        self.declare_parameter('stt_model', 'whisper-large-v3-turbo')
        self.declare_parameter('vosk_model', '')              # '' = pas de secours local
        self.declare_parameter('mute_tail_s', 0.6)            # écho de la fin de phrase du robot
        g = lambda n: self.get_parameter(n).value  # noqa: E731

        self._cfg = li.SegmenterConfig()
        self._seg = li.Segmenter(self._cfg)
        self._vad = self._make_vad(str(g('vad')), int(g('webrtc_aggressiveness')))
        self._stt = [tr.GroqWhisper(str(g('stt_model')))]
        if g('vosk_model'):
            try:
                self._stt.append(tr.VoskStt(str(g('vosk_model'))))
            except tr.SttError as exc:
                self.get_logger().warn(f'pas de secours local : {exc}')
        if not self._stt[0].key:
            self.get_logger().warn('GROQ_API_KEY absente : transcription en ligne impossible')
        self._device = str(g('audio_device'))
        self._tail = float(g('mute_tail_s'))
        self._speaking = False
        self._muted_until = 0.0
        self._enabled = True
        self._pub = self.create_publisher(String, '/musia/question', 10)
        self.create_subscription(Bool, '/musia/speaking', self._on_speaking, 10)
        self.create_subscription(Bool, '/musia/listen', lambda m: setattr(self, '_enabled', m.data), 10)
        self._running = True
        threading.Thread(target=self._capture, daemon=True).start()

    def _make_vad(self, kind: str, aggressiveness: int):
        if kind == 'webrtc':
            try:
                import webrtcvad
                v = webrtcvad.Vad(aggressiveness)
                return lambda f: v.is_speech(f, self._cfg.sample_rate)
            except ImportError:
                self.get_logger().warn('webrtcvad absent : détection par énergie')
        return li.EnergyVad().is_speech

    def _on_speaking(self, m: Bool) -> None:
        self._speaking = m.data
        if not m.data:
            self._muted_until = time.monotonic() + self._tail
        self._seg.reset()          # ce qui a été capté pendant que le robot parlait est jeté

    def _listening(self) -> bool:
        return self._enabled and not self._speaking and time.monotonic() >= self._muted_until

    def _capture(self) -> None:
        cmd = ['arecord', '-q', '-D', self._device, '-f', 'S16_LE', '-c', '1',
               '-r', str(self._cfg.sample_rate), '-t', 'raw']
        n = self._cfg.frame_bytes
        while self._running:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except OSError as exc:
                self.get_logger().error(f'arecord introuvable : {exc}')
                return
            self.get_logger().info(f'écoute sur {self._device}')
            while self._running:
                frame = proc.stdout.read(n)
                if len(frame) < n:
                    break
                if not self._listening():
                    continue
                pcm = self._seg.feed(frame, self._vad(frame))
                if pcm:
                    threading.Thread(target=self._transcribe, args=(pcm,), daemon=True).start()
            proc.kill()
            err = proc.stderr.read().decode(errors='replace').strip() if proc.stderr else ''
            self.get_logger().error(f'capture audio interrompue : {err} — nouvel essai dans 2 s')
            time.sleep(2.0)

    def _transcribe(self, pcm: bytes) -> None:
        try:
            text, engine = tr.transcribe_first(self._stt, li.to_wav(pcm, self._cfg.sample_rate))
        except tr.SttError as exc:
            self.get_logger().warn(f'transcription impossible : {exc}')
            return
        if li.worth_sending(text):
            self.get_logger().info(f'entendu ({engine}) : {text!r}')
            self._pub.publish(String(data=text))

    def destroy_node(self) -> None:
        self._running = False
        super().destroy_node()


def _spin(cls, threads: int = 1) -> None:
    rclpy.init()
    node = cls()
    ex = MultiThreadedExecutor(num_threads=threads) if threads > 1 else None
    try:
        if ex:
            ex.add_node(node)
            ex.spin()
        else:
            rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


def say_main() -> None:
    _spin(SayNode, threads=4)      # l'annulation doit passer pendant qu'une phrase se joue


def listen_main() -> None:
    _spin(ListenNode)
