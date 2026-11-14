# Permet de lancer tous les tests d'un coup, sans ROS, depuis ros2_ws/src :
#     python -m pytest
# (sur le robot, « colcon test » lance les tests paquet par paquet et n'a pas besoin de ce fichier).
import sys
from pathlib import Path

for pkg in ('musia_base', 'musia_tour', 'musia_hardware', 'musia_voice', 'musia_pilot'):
    sys.path.insert(0, str(Path(__file__).parent / pkg))
