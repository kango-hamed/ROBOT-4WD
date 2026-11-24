"""Serveur HTTP de la page de pilotage (bibliothèque standard uniquement, sans ROS).

Le serveur ne parle pas ROS : il appelle un objet « robot » (pilot_node.RosRobot sur le
Pi, un faux robot dans les tests) qui fournit :

    state() -> dict                     position, navigation, batterie, carte…
    current_pose() -> (x, y, yaw) | None
    map_png() -> bytes | None
    drive(linear_mps, angular_rps)      pilotage manuel (/cmd_vel_teleop)
    goal(x, y, yaw)                     destination (/goal_pose)
    cancel()                            annule la destination
    set_estop(on)                       arrêt d'urgence
    head(deg)                           orientation de la tête

API (JSON) :
    GET    /api/state           GET  /api/map.png        GET /api/points
    POST   /api/drive {x, z}    x, z entre -1 et 1 (fractions des vitesses maximales)
    POST   /api/goal {x, y, yaw?}  sans yaw : orienté du robot vers la destination
    POST   /api/cancel          POST /api/estop {on}     POST /api/head {deg}
    POST   /api/points {name, x?, y?, yaw?}   sans coordonnées : position actuelle
    POST   /api/points/<nom>/go               DELETE /api/points/<nom>

Les vitesses maximales sont appliquées ici, côté robot : la page ne peut pas les dépasser.
Aucune authentification : à n'utiliser que sur un réseau Wi-Fi de confiance (README).
"""

import json
import math
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from .points import PointError, PointsStore

MAX_BODY = 4096


class BadRequest(ValueError):
    pass


def number(body: dict, key: str, lo=None, hi=None, default=None) -> float:
    v = body.get(key, default)
    if v is None:
        raise BadRequest(f'« {key} » manquant')
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise BadRequest(f'« {key} » doit être un nombre')
    v = float(v)
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def heading_to(pose, x: float, y: float) -> float:
    """Cap du robot vers (x, y) ; 0 si la position est inconnue ou confondue."""
    if pose is None or math.hypot(x - pose[0], y - pose[1]) < 1e-3:
        return 0.0 if pose is None else pose[2]
    return math.atan2(y - pose[1], x - pose[0])


class PilotServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, robot, points: PointsStore, static_dir: str,
                 max_linear: float = 0.25, max_angular: float = 1.5, head_limit_deg: float = 80.0):
        super().__init__(addr, _Handler)
        self.robot = robot
        self.points = points
        self.static_dir = os.path.abspath(static_dir)
        self.max_linear = max_linear
        self.max_angular = max_angular
        self.head_limit_deg = head_limit_deg


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'   # connexion maintenue : le joystick envoie 10 requêtes/s
    server: PilotServer

    def log_message(self, *args):   # pas de journal par requête (10/s pendant le pilotage)
        pass

    # --- réponses ---------------------------------------------------------------------
    def _send(self, status, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, obj, status=HTTPStatus.OK) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def _error(self, status, message: str) -> None:
        self._json({'error': message}, status)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError as exc:
            self.close_connection = True
            raise BadRequest('Content-Length invalide') from exc
        if length > MAX_BODY:
            # lire et jeter le corps : sinon le client reçoit une connexion coupée, pas l'erreur
            if length <= 1 << 20:
                self.rfile.read(length)
            else:
                self.close_connection = True
            raise BadRequest('requête trop longue')
        if length == 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError as exc:
            raise BadRequest('JSON invalide') from exc
        if not isinstance(body, dict):
            raise BadRequest('un objet JSON est attendu')
        return body

    # --- routage ----------------------------------------------------------------------
    def do_GET(self):
        self._dispatch('GET')

    def do_HEAD(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def do_DELETE(self):
        self._dispatch('DELETE')

    def _dispatch(self, method: str) -> None:
        path = unquote(urlsplit(self.path).path)
        try:
            if path.startswith('/api/'):
                self._api(method, path[len('/api/'):].strip('/').split('/'))
            elif method == 'GET':
                self._static(path)
            else:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, 'méthode non permise')
        except BadRequest as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except PointError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))

    def _api(self, method: str, parts: list) -> None:
        srv, robot = self.server, self.server.robot
        route = (method, parts[0], len(parts))

        if route == ('GET', 'state', 1):
            return self._json(robot.state())
        if route == ('GET', 'map.png', 1):
            png = robot.map_png()
            if png is None:
                return self._error(HTTPStatus.NOT_FOUND, 'pas encore de carte')
            return self._send(HTTPStatus.OK, png, 'image/png')
        if route == ('POST', 'drive', 1):
            body = self._body()
            robot.drive(number(body, 'x', -1, 1) * srv.max_linear,
                        number(body, 'z', -1, 1) * srv.max_angular)
            return self._json({'ok': True})
        if route == ('POST', 'goal', 1):
            body = self._body()
            x, y = number(body, 'x'), number(body, 'y')
            yaw = body.get('yaw')
            yaw = heading_to(robot.current_pose(), x, y) if yaw is None else number(body, 'yaw')
            robot.goal(x, y, yaw)
            return self._json({'ok': True, 'x': x, 'y': y, 'yaw': yaw})
        if route == ('POST', 'cancel', 1):
            robot.cancel()
            return self._json({'ok': True})
        if route == ('POST', 'estop', 1):
            on = self._body().get('on')
            if not isinstance(on, bool):
                raise BadRequest('« on » doit valoir true ou false')
            robot.set_estop(on)
            return self._json({'ok': True, 'estop': on})
        if route == ('POST', 'head', 1):
            lim = srv.head_limit_deg
            deg = number(self._body(), 'deg', -lim, lim)
            robot.head(deg)
            return self._json({'ok': True, 'deg': deg})

        if parts[0] == 'points':
            return self._points(method, parts[1:])
        return self._error(HTTPStatus.NOT_FOUND, 'route inconnue')

    def _points(self, method: str, rest: list) -> None:
        store, robot = self.server.points, self.server.robot
        if method == 'GET' and not rest:
            return self._json({'points': store.list()})
        if method == 'POST' and not rest:
            body = self._body()
            if 'x' in body or 'y' in body:
                x, y = number(body, 'x'), number(body, 'y')
                yaw = number(body, 'yaw', default=0.0)
            else:
                pose = robot.current_pose()
                if pose is None:
                    return self._error(HTTPStatus.CONFLICT, 'position du robot inconnue')
                x, y, yaw = pose
            return self._json(store.put(body.get('name'), x, y, yaw), HTTPStatus.CREATED)
        if method == 'POST' and len(rest) == 2 and rest[1] == 'go':
            point = store.get(rest[0])
            if point is None:
                return self._error(HTTPStatus.NOT_FOUND, f'point « {rest[0]} » inconnu')
            robot.goal(*point)
            return self._json({'ok': True, 'name': rest[0]})
        if method == 'DELETE' and len(rest) == 1:
            if not store.delete(rest[0]):
                return self._error(HTTPStatus.NOT_FOUND, f'point « {rest[0]} » inconnu')
            return self._json({'ok': True})
        return self._error(HTTPStatus.NOT_FOUND, 'route inconnue')

    def _static(self, path: str) -> None:
        root = self.server.static_dir
        # abspath et non realpath : avec « colcon build --symlink-install », les fichiers
        # installés sont des liens vers build/, hors du dossier. abspath normalise « .. »,
        # ce qui suffit à bloquer la sortie du dossier.
        full = os.path.abspath(os.path.join(root, (path.lstrip('/') or 'index.html')))
        if os.path.commonpath([root, full]) != root or not os.path.isfile(full):
            return self._error(HTTPStatus.NOT_FOUND, 'fichier introuvable')
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype.endswith('javascript'):
            ctype += '; charset=utf-8'
        with open(full, 'rb') as f:
            self._send(HTTPStatus.OK, f.read(), ctype)
