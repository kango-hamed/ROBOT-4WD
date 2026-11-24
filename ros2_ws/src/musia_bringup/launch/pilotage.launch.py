"""Pilotage à distance seul : moteurs + webcam + page de pilotage, sans navigation.

    ros2 launch musia_bringup pilotage.launch.py
    ros2 launch musia_bringup pilotage.launch.py camera:=false     # sans webcam
    ros2 launch musia_bringup pilotage.launch.py head:=true        # avec le servo de la tête

Puis, depuis un téléphone ou un PC sur le même Wi-Fi : http://<adresse-du-robot>:8080/

    page de pilotage ─▶ pilot_node ─▶ /cmd_vel_teleop ─▶ twist_mux ─▶ /cmd_vel ─▶ base_node ─▶ Arduino
    arrêt d'urgence ─▶ /musia/estop (verrou) ─────────────┘
    webcam ─▶ /camera/image_raw ─▶ web_video_server (port 8081) ─▶ vidéo de la page

Sert à valider la chaîne moteurs et la vidéo avant le lidar et Nav2 (prototype.launch.py).
La carte et les destinations de la page restent vides : rien ne publie /map.
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
    config = os.path.join(share, 'config')
    camera = LaunchConfiguration('camera')

    return LaunchDescription([
        DeclareLaunchArgument('camera', default_value='true'),
        DeclareLaunchArgument('head', default_value='false'),

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(share, 'launch', 'robot.launch.py')),
            launch_arguments={'camera': camera, 'head': LaunchConfiguration('head'),
                              'lidar': 'false', 'imu': 'false', 'battery': 'false'}.items()),

        Node(package='twist_mux', executable='twist_mux', name='twist_mux', output='screen',
             parameters=[os.path.join(config, 'twist_mux.yaml')],
             remappings=[('cmd_vel_out', '/cmd_vel')]),
        Node(package='web_video_server', executable='web_video_server', name='web_video_server',
             output='screen', condition=IfCondition(camera),
             parameters=[{'port': 8081, 'default_stream_type': 'mjpeg'}]),
        Node(package='musia_pilot', executable='pilot_node', name='pilot_node', output='screen',
             parameters=[os.path.join(config, 'robot.yaml')]),
    ])
