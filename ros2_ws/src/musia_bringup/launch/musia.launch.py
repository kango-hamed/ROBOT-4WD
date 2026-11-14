"""MUSIA complet : matériel + navigation + visite guidée.

EN PAUSE : la visite guidée attend la suite du projet. Pour le prototype, utiliser
prototype.launch.py (navigation A → B et pilotage). Ce lancement n'inclut pas twist_mux.

    ros2 launch musia_bringup musia.launch.py
    ros2 launch musia_bringup musia.launch.py tour_file:=/chemin/autre_visite.yaml map:=/chemin/carte.yaml

Démarrer la visite sans attendre un visiteur :
    ros2 service call /musia/tour/command musia_interfaces/srv/TourCommand "{command: start}"
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('musia_bringup')
    launch_dir = os.path.join(share, 'launch')
    params = os.path.join(share, 'config', 'robot.yaml')
    return LaunchDescription([
        DeclareLaunchArgument('map', default_value=os.path.join(share, 'maps', 'famienkro.yaml')),
        DeclareLaunchArgument('tour_file', default_value=os.path.join(share, 'config', 'visite_famienkro.yaml')),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(launch_dir, 'robot.launch.py'))),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(launch_dir, 'navigation.launch.py')),
                                 launch_arguments={'map': LaunchConfiguration('map')}.items()),
        Node(package='musia_tour', executable='tour_node', name='tour_node', output='screen',
             parameters=[params, {'tour_file': LaunchConfiguration('tour_file')}]),
    ])
