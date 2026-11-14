from pathlib import Path

import pytest

from musia_tour import tour_file
from musia_tour.engine import Ask, CancelNav, Config, GoTo, Led, Phase, Say, StopSpeech, TourEngine

VISITE = Path(__file__).resolve().parents[2] / 'musia_bringup' / 'config' / 'visite_famienkro.yaml'


@pytest.fixture
def tour():
    return tour_file.load_file(str(VISITE))


@pytest.fixture
def eng(tour):
    return TourEngine(tour, Config(listen_s=10, answer_timeout_s=5, nav_retries=1, cooldown_s=20))


def only(actions, kind):
    found = [a for a in actions if isinstance(a, kind)]
    assert len(found) == 1, actions
    return found[0]


def reach_listening(eng, now=0.0):
    """Visiteur -> accueil -> arrivée au 1er arrêt -> présentation -> écoute ouverte."""
    say = only(eng.person_seen(now), Say)
    go = only(eng.speech_done(say.token, now), GoTo)
    pres = only(eng.nav_done(go.token, True, now), Say)
    inv = only(eng.speech_done(pres.token, now), Say)
    assert eng.speech_done(inv.token, now) == []
    assert eng.phase is Phase.ECOUTE


def test_tour_file_loads(tour):
    assert [s.id for s in tour.stops] == ['goli', 'kita', 'gunye-ge', 'kpelie']
    assert tour.home.x == 0.0


def test_full_tour_without_questions(eng, tour):
    t = 0.0
    reach_listening(eng, t)
    for i in range(1, len(tour.stops)):
        t += 11
        go = only(eng.tick(t), GoTo)
        assert eng.phase is Phase.GUIDAGE and eng.index == i
        pres = only(eng.nav_done(go.token, True, t), Say)
        assert pres.text == tour.stops[i].presentation
        inv = only(eng.speech_done(pres.token, t), Say)
        eng.speech_done(inv.token, t)
    t += 11
    home = only(eng.tick(t), GoTo)
    assert eng.phase is Phase.RETOUR and home.pose == tour.home
    assert eng.nav_done(home.token, True, t) == [Led('veille')]
    assert eng.phase is Phase.VEILLE


def test_question_and_answer_then_listen_again(eng):
    reach_listening(eng)
    ask = only(eng.question('Que représente le Goli ?', 1.0), Ask)
    assert ask.stop_id == 'goli' and eng.phase is Phase.REPONSE
    say = only(eng.answer(ask.token, 'Le soleil.', 2.0), Say)
    assert say.text == 'Le soleil.'
    assert eng.speech_done(say.token, 3.0) == [Led('ecoute')]
    assert eng.phase is Phase.ECOUTE
    assert eng.tick(12.9) == []                     # l'écoute repart de la fin de la réponse
    assert isinstance(only(eng.tick(13.1), GoTo), GoTo)


def test_answer_timeout_says_no_answer(eng, tour):
    reach_listening(eng)
    ask = only(eng.question('Combien ça coûte ?', 0.0), Ask)
    say = only(eng.tick(6.0), Say)
    assert say.text == tour.no_answer
    assert eng.answer(ask.token, 'trop tard', 7.0) == []    # réponse périmée ignorée


def test_question_during_presentation_is_kept(eng):
    say = only(eng.person_seen(0), Say)
    go = only(eng.speech_done(say.token, 0), GoTo)
    pres = only(eng.nav_done(go.token, True, 0), Say)
    assert eng.question('Qui a fait ce masque ?', 1) == []
    inv = only(eng.speech_done(pres.token, 2), Say)
    ask = only(eng.speech_done(inv.token, 3), Ask)
    assert ask.question == 'Qui a fait ce masque ?'


def test_stale_speech_and_nav_results_are_ignored(eng):
    say = only(eng.person_seen(0), Say)
    assert eng.speech_done(say.token + 99, 0) == []
    go = only(eng.speech_done(say.token, 0), GoTo)
    assert eng.nav_done(go.token + 99, True, 0) == []
    assert eng.phase is Phase.GUIDAGE


def test_nav_failure_retries_then_skips(eng, tour):
    say = only(eng.person_seen(0), Say)
    go = only(eng.speech_done(say.token, 0), GoTo)
    retry = only(eng.nav_done(go.token, False, 0), GoTo)
    assert retry.pose == tour.stops[0].pose
    actions = eng.nav_done(retry.token, False, 0)
    texts = [a.text for a in actions if isinstance(a, Say)]
    assert texts[0] == tour.nav_failed.format(zone='salle Baoulé')
    assert only(actions, GoTo).pose == tour.stops[1].pose and eng.index == 1


def test_stop_command_and_cooldown(eng):
    reach_listening(eng, 0)
    ok, _, actions = eng.command('stop', 5)
    assert ok and StopSpeech() in actions and CancelNav() in actions
    assert eng.phase is Phase.VEILLE
    assert eng.person_seen(10) == []                # visiteur ignoré pendant 20 s
    assert eng.person_seen(26)


def test_commands_rejected_when_meaningless(eng):
    assert eng.command('next', 0)[0] is False
    assert eng.command('home', 0)[0] is False
    assert eng.command('danse', 0)[0] is False
    assert eng.command('start', 0)[0] is True
    assert eng.command('start', 0)[0] is False


def test_home_command_returns(eng, tour):
    reach_listening(eng)
    ok, _, actions = eng.command('home', 1)
    assert ok and only(actions, GoTo).pose == tour.home and eng.phase is Phase.RETOUR


def test_local_answers_whole_words(tour):
    goli = tour.stops[0]
    assert 'soleil' in tour_file.local_answer(tour, goli, 'Pourquoi le SOLEIL ?')
    assert 'Sénoufo' in tour_file.local_answer(tour, None, 'Parlez-moi des Sénoufo')
    # « dans » ne doit pas déclencher la réponse sur les Dan
    assert tour_file.local_answer(tour, None, "Qu'y a-t-il dans cette salle ?") is None


def test_tour_file_errors():
    with pytest.raises(tour_file.TourFileError):
        tour_file.load('nom: x\n')
