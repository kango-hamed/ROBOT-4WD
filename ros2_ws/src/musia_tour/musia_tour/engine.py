"""Machine à états de la visite guidée — sans ROS, entièrement testable.

Le moteur reçoit des événements (visiteur vu, phrase terminée, arrivée, question…) et
renvoie des actions (parler, aller à, demander une réponse…). C'est tour_node qui les
exécute avec ROS 2 et lui renvoie les résultats.

Différences avec l'ancien tour_manager :
  * la fin d'une phrase est signalée par la voix (action Say), plus par une durée estimée ;
  * chaque phrase et chaque déplacement porte un jeton : un résultat périmé (phrase
    interrompue, navigation annulée) est ignoré au lieu de faire avancer la visite ;
  * le mode « dégradé » n'est plus un état : être hors ligne change seulement la source
    des réponses (tour_node), la visite continue normalement ;
  * la présentation se termine d'elle-même : invitation aux questions, écoute pendant
    listen_s secondes, puis arrêt suivant (l'ancien état PRESENTATION ne sortait jamais) ;
  * un échec de navigation est réessayé, puis l'arrêt est sauté avec une annonce.

    VEILLE ─visiteur/start─▶ ACCUEIL ─fin─▶ GUIDAGE ─arrivé─▶ PRESENTATION ─fin─▶ ECOUTE
       ▲                                       ▲                                 │  ▲
       │                                       └────── délai écoulé / next ──────┤  │
       └── arrivé ── RETOUR ◀── dernier arrêt / home ─────────────────────────────┘  │
                                                    question ▶ REPONSE ─fin réponse──┘
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from enum import Enum

from .tour_file import Pose, Stop, Tour


class Phase(Enum):
    VEILLE = 'VEILLE'
    ACCUEIL = 'ACCUEIL'
    GUIDAGE = 'GUIDAGE'
    PRESENTATION = 'PRESENTATION'
    ECOUTE = 'ECOUTE'
    REPONSE = 'REPONSE'
    RETOUR = 'RETOUR'


IN_TOUR = (Phase.GUIDAGE, Phase.PRESENTATION, Phase.ECOUTE, Phase.REPONSE)


# --- actions (moteur -> tour_node) ------------------------------------------------
@dataclass(frozen=True)
class Say:
    text: str
    token: int


@dataclass(frozen=True)
class StopSpeech:
    pass


@dataclass(frozen=True)
class GoTo:
    pose: Pose
    token: int


@dataclass(frozen=True)
class CancelNav:
    pass


@dataclass(frozen=True)
class Ask:
    question: str
    stop_id: str
    token: int


@dataclass(frozen=True)
class Led:
    pattern: str            # veille, accueil, deplacement, presentation, ecoute, reflexion, retour


Action = Say | StopSpeech | GoTo | CancelNav | Ask | Led


@dataclass(frozen=True)
class Config:
    listen_s: float = 12.0          # écoute des questions après l'invitation
    answer_timeout_s: float = 8.0   # au-delà, réponse « je ne sais pas »
    nav_retries: int = 1
    cooldown_s: float = 20.0        # après une visite, on ignore les visiteurs un moment


class TourEngine:
    def __init__(self, tour: Tour, config: Config = Config()) -> None:
        self.tour, self.cfg = tour, config
        self.phase = Phase.VEILLE
        self.index = 0
        self._tokens = itertools.count(1)
        self._speech: int | None = None      # phrase dont on attend la fin
        self._nav: int | None = None         # déplacement dont on attend le résultat
        self._ask: int | None = None         # question dont on attend la réponse
        self._deadline: float | None = None
        self._pending_question: str | None = None
        self._nav_attempts = 0
        self._cooldown_until = 0.0

    # --- lecture ---------------------------------------------------------------------
    @property
    def stop(self) -> Stop | None:
        return self.tour.stops[self.index] if self.phase in IN_TOUR else None

    # --- événements ------------------------------------------------------------------
    def person_seen(self, now: float) -> list[Action]:
        if self.phase is Phase.VEILLE and now >= self._cooldown_until:
            return self._welcome()
        return []

    def speech_done(self, token: int, now: float) -> list[Action]:
        if token != self._speech:
            return []                       # phrase périmée ou sans importance
        self._speech = None
        if self.phase is Phase.ACCUEIL:
            return self._go_to_stop(0)
        if self.phase is Phase.PRESENTATION:
            self.phase = Phase.ECOUTE
            return [Led('ecoute'), self._say(self.tour.invitation)]
        if self.phase is Phase.ECOUTE:      # fin de l'invitation : l'écoute commence
            return self._start_listening(now)
        if self.phase is Phase.REPONSE:     # réponse dite : on réécoute
            self.phase = Phase.ECOUTE
            return [Led('ecoute')] + self._start_listening(now)
        return []

    def nav_done(self, token: int, success: bool, now: float) -> list[Action]:
        if token != self._nav:
            return []
        self._nav = None
        if self.phase is Phase.RETOUR:
            return self._to_idle(now)
        if self.phase is not Phase.GUIDAGE:
            return []
        if success:
            self.phase = Phase.PRESENTATION
            return [Led('presentation'), self._say(self.stop.presentation)]
        self._nav_attempts += 1
        if self._nav_attempts <= self.cfg.nav_retries:
            return [self._goto(self.stop.pose)]
        failed = self._say(self.tour.nav_failed.format(zone=self.stop.zone))
        return [failed] + self._next_stop()

    def question(self, text: str, now: float) -> list[Action]:
        text = text.strip()
        if not text:
            return []
        if self.phase is Phase.ECOUTE:
            return self._ask_question(text, now)
        if self.phase in (Phase.PRESENTATION, Phase.REPONSE):
            self._pending_question = text   # traitée dès la fin de la phrase en cours
        return []

    def answer(self, token: int, text: str | None, now: float) -> list[Action]:
        if token != self._ask or self.phase is not Phase.REPONSE:
            return []
        self._ask, self._deadline = None, None
        return [self._say(text.strip() if text and text.strip() else self.tour.no_answer)]

    def tick(self, now: float) -> list[Action]:
        if self._deadline is None or now < self._deadline:
            return []
        self._deadline = None
        if self.phase is Phase.ECOUTE:
            return self._next_stop()
        if self.phase is Phase.REPONSE and self._ask is not None:
            self._ask = None
            return [self._say(self.tour.no_answer)]
        return []

    def command(self, name: str, now: float) -> tuple[bool, str, list[Action]]:
        name = name.strip().lower()
        if name == 'start':
            if self.phase is not Phase.VEILLE:
                return False, 'une visite est déjà en cours', []
            return True, 'visite démarrée', self._welcome()
        if name == 'stop':
            if self.phase is Phase.VEILLE:
                return True, 'déjà en veille', []
            return True, 'arrêt', [StopSpeech(), CancelNav()] + self._to_idle(now)
        if name == 'next':
            if self.phase not in IN_TOUR:
                return False, 'aucune visite en cours', []
            return True, 'arrêt suivant', [StopSpeech(), CancelNav()] + self._next_stop()
        if name == 'home':
            if self.phase in (Phase.VEILLE, Phase.RETOUR):
                return False, 'pas de visite à terminer', []
            return True, 'retour au départ', [StopSpeech(), CancelNav()] + self._return_home()
        return False, f'commande inconnue : {name}', []

    # --- transitions -----------------------------------------------------------------
    def _welcome(self) -> list[Action]:
        self._reset_waits()
        self.phase = Phase.ACCUEIL
        return [Led('accueil'), self._say(self.tour.welcome)]

    def _go_to_stop(self, i: int) -> list[Action]:
        self._reset_waits()
        self.phase, self.index, self._nav_attempts = Phase.GUIDAGE, i, 0
        stop = self.tour.stops[i]
        transition = Say(self.tour.transition.format(zone=stop.zone), next(self._tokens))
        return [Led('deplacement'), transition, self._goto(stop.pose)]

    def _next_stop(self) -> list[Action]:
        if self.index + 1 < len(self.tour.stops):
            return self._go_to_stop(self.index + 1)
        return self._return_home()

    def _return_home(self) -> list[Action]:
        self._reset_waits()
        self.phase = Phase.RETOUR
        farewell = Say(self.tour.farewell, next(self._tokens))   # on n'attend pas sa fin
        return [Led('retour'), farewell, self._goto(self.tour.home)]

    def _to_idle(self, now: float) -> list[Action]:
        self._reset_waits()
        self.phase, self.index = Phase.VEILLE, 0
        self._cooldown_until = now + self.cfg.cooldown_s
        return [Led('veille')]

    def _start_listening(self, now: float) -> list[Action]:
        if self._pending_question:
            q, self._pending_question = self._pending_question, None
            return self._ask_question(q, now)
        self._deadline = now + self.cfg.listen_s
        return []

    def _ask_question(self, text: str, now: float) -> list[Action]:
        self.phase = Phase.REPONSE
        self._ask = next(self._tokens)
        self._deadline = now + self.cfg.answer_timeout_s
        return [Led('reflexion'), Ask(text, self.stop.id, self._ask)]

    # --- utilitaires -----------------------------------------------------------------
    def _say(self, text: str) -> Say:
        self._speech = next(self._tokens)
        return Say(text, self._speech)

    def _goto(self, pose: Pose) -> GoTo:
        self._nav = next(self._tokens)
        return GoTo(pose, self._nav)

    def _reset_waits(self) -> None:
        self._speech = self._nav = self._ask = None
        self._deadline = None
        self._pending_question = None
