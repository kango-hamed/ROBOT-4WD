import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from musia_tour import tour_file
from musia_tour.answers import Answerer

VISITE = Path(__file__).resolve().parents[2] / 'musia_bringup' / 'config' / 'visite_famienkro.yaml'
TOUR = tour_file.load_file(str(VISITE))


def serve(reply):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers['Content-Length'])
            H.received = json.loads(self.rfile.read(n))
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, H


def test_server_answer_is_used():
    srv, h = serve({'answer': 'Réponse du serveur.'})
    try:
        a = Answerer(TOUR, f'http://127.0.0.1:{srv.server_port}', timeout_s=2)
        assert a.answer('Qui ?', 'goli') == ('Réponse du serveur.', 'serveur')
        assert h.received == {'question': 'Qui ?', 'stop_id': 'goli', 'tour': TOUR.name}
        assert a.online
    finally:
        srv.shutdown()


def test_falls_back_to_local_when_server_is_down():
    a = Answerer(TOUR, 'http://127.0.0.1:9', timeout_s=0.5)     # port 9 : rien n'écoute
    text, source = a.answer('Parlez-moi du Poro', 'kpelie')
    assert source == 'local' and 'Poro' in text and not a.online


def test_offline_without_url():
    assert Answerer(TOUR).answer('Quelle heure est-il ?', 'goli') == (None, 'local')
