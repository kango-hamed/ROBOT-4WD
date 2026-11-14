"""Tests sans ROS : points, image de carte, état de navigation, serveur HTTP (faux robot)."""

import json
import math
import struct
import threading
import urllib.error
import urllib.request
import zlib

import pytest

from musia_pilot.mapimage import (FREE, OCCUPIED, UNKNOWN, occupancy_png, occupancy_to_rows,
                                  quaternion_from_yaw, yaw_from_quaternion)
from musia_pilot.navstatus import summarize
from musia_pilot.points import PointError, PointsStore, check_name
from musia_pilot.web import PilotServer, heading_to


# --- points ------------------------------------------------------------------------------
def test_points_roundtrip(tmp_path):
    path = tmp_path / 'sub' / 'points.json'
    store = PointsStore(str(path))
    store.put('A', 1, 2, 0.5)
    store.put(' accueil ', -1.5, 0)
    again = PointsStore(str(path))
    assert again.list() == [{'name': 'A', 'x': 1.0, 'y': 2.0, 'yaw': 0.5},
                            {'name': 'accueil', 'x': -1.5, 'y': 0.0, 'yaw': 0.0}]
    assert again.delete('A') and not again.delete('A')
    assert [p['name'] for p in PointsStore(str(path)).list()] == ['accueil']


@pytest.mark.parametrize('bad', ['', ' ', 'a/b', '../x', 'x' * 33, None, 3])
def test_bad_names(bad):
    with pytest.raises(PointError):
        check_name(bad)


def test_accented_name_ok():
    assert check_name('entrée-1') == 'entrée-1'


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), '1', True])
def test_bad_coordinates(tmp_path, bad):
    with pytest.raises(PointError):
        PointsStore(str(tmp_path / 'p.json')).put('A', bad, 0)


def test_corrupt_file_set_aside(tmp_path):
    path = tmp_path / 'points.json'
    path.write_text('{pas du json', encoding='utf-8')
    assert PointsStore(str(path)).list() == []
    assert (tmp_path / 'points.json.corrompu').exists()


# --- carte -------------------------------------------------------------------------------
def test_occupancy_rows_flipped_and_mapped():
    # 2×2 : ligne 0 (bas, y minimal) = [libre, occupé], ligne 1 (haut) = [inconnu, 50 %]
    data = bytes([0, 100, 255, 50])          # 255 = -1 en int8
    rows = occupancy_to_rows(2, 2, data)
    assert rows == bytes([UNKNOWN, 127, FREE, OCCUPIED])


def test_occupancy_size_checked():
    with pytest.raises(ValueError):
        occupancy_to_rows(3, 3, bytes(8))


def test_png_decodes():
    png = occupancy_png(3, 2, bytes([0, 100, 255, 0, 0, 0]))
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    width, height = struct.unpack('>II', png[16:24])
    assert (width, height) == (3, 2)
    idat_len = struct.unpack('>I', png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_len])
    assert raw == b'\x00' + bytes([FREE] * 3) + b'\x00' + bytes([FREE, OCCUPIED, UNKNOWN])


@pytest.mark.parametrize('yaw', [0.0, 1.0, -2.5, math.pi - 0.01])
def test_yaw_roundtrip(yaw):
    assert yaw_from_quaternion(*quaternion_from_yaw(yaw)) == pytest.approx(yaw)


# --- état de navigation ------------------------------------------------------------------
def test_nav_summary():
    assert summarize([]) == 'repos'
    assert summarize([(1, 2)]) == 'en_route'
    assert summarize([(5, 6), (9, 4)]) == 'arrive'      # le plus récent l'emporte
    assert summarize([(9, 6), (5, 4)]) == 'echec'
    assert summarize([(1, 42)]) == 'inconnu'


def test_heading_to():
    assert heading_to(None, 1, 1) == 0.0
    assert heading_to((0, 0, 0.3), 0, 1) == pytest.approx(math.pi / 2)
    assert heading_to((2, 2, 0.3), 2, 2) == 0.3


# --- serveur HTTP ------------------------------------------------------------------------
class FakeRobot:
    def __init__(self):
        self.calls = []
        self.pose = (1.0, 2.0, 0.5)
        self.png = None

    def state(self):
        return {'nav': 'repos', 'pose': self.pose}

    def current_pose(self):
        return self.pose

    def map_png(self):
        return self.png

    def drive(self, linear, angular):
        self.calls.append(('drive', linear, angular))

    def goal(self, x, y, yaw):
        self.calls.append(('goal', x, y, yaw))

    def cancel(self):
        self.calls.append(('cancel',))

    def set_estop(self, on):
        self.calls.append(('estop', on))

    def head(self, deg):
        self.calls.append(('head', deg))


@pytest.fixture
def server(tmp_path):
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('<p>pilotage</p>', encoding='utf-8')
    (tmp_path / 'secret.txt').write_text('ne pas servir', encoding='utf-8')
    robot = FakeRobot()
    srv = PilotServer(('127.0.0.1', 0), robot, PointsStore(str(tmp_path / 'points.json')),
                      str(static), max_linear=0.2, max_angular=1.0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}'

    def call(method, path, body=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=5) as res:
                payload = res.read()
                return res.status, payload if raw else json.loads(payload)
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield call, robot
    srv.shutdown()
    srv.server_close()


def test_static_and_traversal(server):
    call, _ = server
    assert call('GET', '/', raw=True) == (200, b'<p>pilotage</p>')
    assert call('GET', '/../secret.txt')[0] == 404
    assert call('GET', '/%2e%2e/secret.txt')[0] == 404


def test_drive_scaled_and_clamped(server):
    call, robot = server
    assert call('POST', '/api/drive', {'x': 0.5, 'z': -3})[0] == 200
    assert robot.calls == [('drive', pytest.approx(0.1), -1.0)]


@pytest.mark.parametrize('body', [{}, {'x': 'vite', 'z': 0}, {'x': True, 'z': 0}, [1, 2]])
def test_drive_rejects_bad_body(server, body):
    call, robot = server
    assert call('POST', '/api/drive', body)[0] == 400
    assert robot.calls == []


def test_goal_heading_defaults_towards_target(server):
    call, robot = server
    status, res = call('POST', '/api/goal', {'x': 1.0, 'y': 3.0})
    assert status == 200
    assert robot.calls == [('goal', 1.0, 3.0, pytest.approx(math.pi / 2))]
    call('POST', '/api/goal', {'x': 0, 'y': 0, 'yaw': 1.2})
    assert robot.calls[-1] == ('goal', 0.0, 0.0, 1.2)


def test_estop_cancel_head(server):
    call, robot = server
    assert call('POST', '/api/estop', {'on': 'oui'})[0] == 400
    call('POST', '/api/estop', {'on': True})
    call('POST', '/api/cancel')
    call('POST', '/api/head', {'deg': 200})
    assert robot.calls == [('estop', True), ('cancel',), ('head', 80.0)]


def test_points_api(server):
    call, robot = server
    assert call('POST', '/api/points', {'name': 'A'})[1] == {'name': 'A', 'x': 1.0, 'y': 2.0, 'yaw': 0.5}
    assert call('POST', '/api/points', {'name': 'B', 'x': -1, 'y': 0})[0] == 201
    assert [p['name'] for p in call('GET', '/api/points')[1]['points']] == ['A', 'B']
    assert call('POST', '/api/points/B/go')[0] == 200
    assert robot.calls == [('goal', -1.0, 0.0, 0.0)]
    assert call('POST', '/api/points/Z/go')[0] == 404
    assert call('DELETE', '/api/points/A')[0] == 200
    assert call('DELETE', '/api/points/A')[0] == 404
    assert call('POST', '/api/points', {'name': '../x'})[0] == 400


def test_point_needs_pose(server):
    call, robot = server
    robot.pose = None
    assert call('POST', '/api/points', {'name': 'A'})[0] == 409


def test_map_png(server):
    call, robot = server
    assert call('GET', '/api/map.png')[0] == 404
    robot.png = occupancy_png(1, 1, b'\x00')
    status, body = call('GET', '/api/map.png', raw=True)
    assert status == 200 and body.startswith(b'\x89PNG')


def test_body_too_long(server):
    call, robot = server
    assert call('POST', '/api/drive', {'x': 0, 'z': 0, 'pad': 'x' * 5000})[0] == 400
