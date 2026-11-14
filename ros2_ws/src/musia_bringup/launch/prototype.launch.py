"""Prototype MUSIA : aller seul d'un point A à un point B, et pilotage à distance.

    ros2 launch musia_bringup prototype.launch.py                 # exploration (sans carte)
    ros2 launch musia_bringup prototype.launch.py slam:=False map:=/home/musia/cartes/salle.yaml
    ros2 launch musia_bringup prototype.launch.py camera:=false   # sans webcam

Puis, depuis un téléphone ou un PC sur le même Wi-Fi : http://<adresse-du-robot>:8080/

    page de pilotage ─▶ pilot_node ─▶ /cmd_vel_teleop ─┐
    Nav2 ─▶ collision_monitor ─▶ /cmd_vel_auto ────────┼─▶ twist_mux ─▶ /cmd_vel ─▶ base_node ─▶ Arduino
    arrêt d'urgence ─▶ /musia/estop (verrou) ──────────┘

Contient : matériel (robot.launch.py), odométrie + Nav2 (navigation.launch.py), twist_mux,
serveur vidéo (port 8081) et page de pilotage (port 8080). La visite guidée et la voix
sont en pause et ne sont pas lancées.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('musia_bringup')
    launch_dir = os.path.join(share, 'launch')
    config = os.path.join(share, 'config')
    camera = LaunchConfiguration('camera')

    return LaunchDescription([
        DeclareLaunchArgument('slam', default_value='True',
                              description='True : exploration sans carte ; False : carte + AMCL'),
        DeclareLaunchArgument('map', default_value=os.path.join(share, 'maps', 'famienkro.yaml')),
        DeclareLaunchArgument('camera', default_value='true'),

        IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(launch_dir, 'robot.launch.py')),
                                 launch_arguments={'camera': camera}.items()),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(launch_dir, 'navigation.launch.py')),
                                 launch_arguments={'slam': LaunchConfiguration('slam'),
                                                   'map': LaunchConfiguration('map')}.items()),

        Node(package='twist_mux', executable='twist_mux', name='twist_mux', output='screen',
             parameters=[os.path.join(config, 'twist_mux.yaml')],
             remappings=[('cmd_vel_out', '/cmd_vel')]),
        Node(package='web_video_server', executable='web_video_server', name='web_video_server',
             output='screen', condition=IfCondition(camera),
             parameters=[{'port': 8081, 'default_stream_type': 'mjpeg'}]),
        Node(package='musia_pilot', executable='pilot_node', name='pilot_node', output='screen',
             parameters=[os.path.join(config, 'robot.yaml')]),
    ])
