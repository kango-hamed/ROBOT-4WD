"""État de la navigation, lu sur navigate_to_pose/_action/status (action_msgs/GoalStatus)."""

# codes de action_msgs/msg/GoalStatus
_STATES = {
    0: 'inconnu',
    1: 'en_route',      # ACCEPTED
    2: 'en_route',      # EXECUTING
    3: 'annulation',    # CANCELING
    4: 'arrive',        # SUCCEEDED
    5: 'annule',        # CANCELED
    6: 'echec',         # ABORTED : chemin introuvable, robot bloqué…
}

ACTIVE = ('en_route', 'annulation')


def summarize(goals) -> str:
    """goals : [(horodatage_ns, code)] ; l'objectif le plus récent donne l'état."""
    if not goals:
        return 'repos'
    _, code = max(goals, key=lambda g: g[0])
    return _STATES.get(code, 'inconnu')
