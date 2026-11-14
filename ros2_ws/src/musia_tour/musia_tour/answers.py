"""Source des réponses : serveur de conversation (cloud) d'abord, réponses locales ensuite.

Contrat avec le serveur (à implémenter côté serveur, étape suivante du projet) :
    POST <conversation_url>/robot/question
    {"question": "...", "stop_id": "goli", "tour": "Visite Famienkro — art ivoirien"}
    -> 200 {"answer": "..."}
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .tour_file import Tour, local_answer


class Answerer:
    def __init__(self, tour: Tour, url: str = '', timeout_s: float = 5.0) -> None:
        self.tour, self.url, self.timeout = tour, url.rstrip('/'), timeout_s
        self.online = False

    def answer(self, question: str, stop_id: str) -> tuple[str | None, str]:
        """Renvoie (réponse ou None, source). Appel bloquant : à lancer hors du fil ROS."""
        if self.url:
            try:
                text = self._ask_server(question, stop_id)
                self.online = True
                if text:
                    return text, 'serveur'
            except (urllib.error.URLError, TimeoutError, ValueError, OSError):
                self.online = False
        stop = next((s for s in self.tour.stops if s.id == stop_id), None)
        return local_answer(self.tour, stop, question), 'local'

    def _ask_server(self, question: str, stop_id: str) -> str | None:
        body = json.dumps({'question': question, 'stop_id': stop_id, 'tour': self.tour.name}).encode()
        req = urllib.request.Request(self.url + '/robot/question', data=body,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        a = data.get('answer') if isinstance(data, dict) else None
        return str(a) if a else None
