"""Fichier de visite (YAML) : arrêts, textes et réponses locales.

Les textes ne sont plus dans le code : on change de musée ou de parcours en changeant
de fichier (plus tard, le serveur fournira ce fichier). Exemple : config/visite_famienkro.yaml.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import yaml


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class LocalAnswer:
    keywords: tuple[str, ...]
    answer: str


@dataclass(frozen=True)
class Stop:
    id: str
    zone: str
    pose: Pose
    presentation: str
    answers: tuple[LocalAnswer, ...] = ()


@dataclass(frozen=True)
class Tour:
    name: str
    home: Pose
    welcome: str
    transition: str            # peut contenir {zone}
    invitation: str            # « Avez-vous des questions ? »
    farewell: str
    no_answer: str
    nav_failed: str            # peut contenir {zone}
    stops: tuple[Stop, ...]
    answers: tuple[LocalAnswer, ...] = field(default=())


class TourFileError(ValueError):
    pass


def _pose(d, where: str) -> Pose:
    try:
        return Pose(float(d['x']), float(d['y']), float(d.get('yaw', 0.0)))
    except (KeyError, TypeError, ValueError) as exc:
        raise TourFileError(f'{where} : pose invalide ({d!r})') from exc


def _answers(items, where: str) -> tuple[LocalAnswer, ...]:
    out = []
    for i, a in enumerate(items or []):
        try:
            out.append(LocalAnswer(tuple(normalize(k) for k in a['mots']), str(a['reponse'])))
        except (KeyError, TypeError) as exc:
            raise TourFileError(f'{where}, réponse {i + 1} : il faut « mots » et « reponse »') from exc
    return tuple(out)


def load(text: str) -> Tour:
    d = yaml.safe_load(text)
    if not isinstance(d, dict):
        raise TourFileError('le fichier de visite doit être un dictionnaire YAML')
    try:
        textes = d['textes']
        stops = []
        for i, s in enumerate(d['arrets']):
            where = f'arrêt {i + 1}'
            stops.append(Stop(id=str(s['id']), zone=str(s['zone']), pose=_pose(s['pose'], where),
                              presentation=str(s['presentation']),
                              answers=_answers(s.get('reponses'), where)))
        if not stops:
            raise TourFileError('la visite doit avoir au moins un arrêt')
        if len({s.id for s in stops}) != len(stops):
            raise TourFileError('deux arrêts ont le même id')
        return Tour(name=str(d['nom']), home=_pose(d['depart'], 'depart'),
                    welcome=textes['accueil'], transition=textes['transition'],
                    invitation=textes['invitation'], farewell=textes['fin'],
                    no_answer=textes['sans_reponse'], nav_failed=textes['navigation_impossible'],
                    stops=tuple(stops), answers=_answers(d.get('reponses_generales'), 'reponses_generales'))
    except KeyError as exc:
        raise TourFileError(f'clé manquante : {exc}') from exc


def load_file(path: str) -> Tour:
    with open(path, encoding='utf-8') as f:
        return load(f.read())


def normalize(s: str) -> str:
    """Minuscules, sans accents : « Sénoufo » et « senoufo » se valent."""
    s = unicodedata.normalize('NFKD', s.lower())
    return ''.join(c for c in s if not unicodedata.combining(c))


def local_answer(tour: Tour, stop: Stop | None, question: str) -> str | None:
    """Réponse hors ligne : d'abord celles de l'arrêt courant, puis les générales."""
    # mots entiers seulement : le mot-clé « dan » ne doit pas répondre à « dans »
    q = ' ' + ' '.join(re.findall(r'[a-z0-9]+', normalize(question))) + ' '
    for a in (stop.answers if stop else ()) + tour.answers:
        if any(' ' + ' '.join(re.findall(r'[a-z0-9]+', k)) + ' ' in q for k in a.keywords):
            return a.answer
    return None
