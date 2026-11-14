"""Points nommés (A, B, accueil…) enregistrés sur le Pi, dans un fichier JSON.

Les coordonnées sont dans le repère « map ». En mode exploration (slam:=True), ce repère
naît là où le robot démarre : un point n'est retrouvé d'une séance à l'autre que si le
robot repart du même endroit, dans la même direction (repère au sol), ou si l'on réutilise
une carte enregistrée (slam:=False). Voir README, Pilotage.
"""

import json
import math
import os
import re
import threading

_NAME = re.compile(r'^[\w \-]{1,32}$')


class PointError(ValueError):
    """Nom ou coordonnées invalides."""


def check_name(name) -> str:
    if not isinstance(name, str):
        raise PointError('le nom doit être un texte')
    name = name.strip()
    if not _NAME.match(name):
        raise PointError('nom : 1 à 32 lettres, chiffres, espaces, - ou _')
    return name


def _finite(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise PointError(f'{what} doit être un nombre')
    return float(v)


class PointsStore:
    """Liste de points {nom: (x, y, yaw)}, sauvegardée à chaque modification."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._points = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding='utf-8') as f:
                raw = json.load(f)
            points = {}
            for p in raw.get('points', []):
                points[check_name(p['name'])] = (_finite(p['x'], 'x'), _finite(p['y'], 'y'),
                                                 _finite(p.get('yaw', 0.0), 'yaw'))
            self._points = points
        except FileNotFoundError:
            self._points = {}
        except (ValueError, KeyError, TypeError, AttributeError):
            # fichier abîmé : on le met de côté plutôt que de l'écraser
            os.replace(self.path, self.path + '.corrompu')
            self._points = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'points': self.list()}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)  # écriture atomique : pas de fichier à moitié écrit

    def list(self) -> list:
        return [{'name': n, 'x': x, 'y': y, 'yaw': yaw}
                for n, (x, y, yaw) in sorted(self._points.items())]

    def get(self, name: str):
        return self._points.get(check_name(name))

    def put(self, name, x, y, yaw=0.0) -> dict:
        name = check_name(name)
        point = (_finite(x, 'x'), _finite(y, 'y'), _finite(yaw, 'yaw'))
        with self._lock:
            self._points[name] = point
            self._save()
        return {'name': name, 'x': point[0], 'y': point[1], 'yaw': point[2]}

    def delete(self, name) -> bool:
        name = check_name(name)
        with self._lock:
            if self._points.pop(name, None) is None:
                return False
            self._save()
        return True
