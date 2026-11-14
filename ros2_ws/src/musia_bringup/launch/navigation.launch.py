"""Localisation et navigation : odométrie laser (rf2o) + EKF, puis Nav2.

Deux modes :
    ros2 launch musia_bringup navigation.launch.py                    # exploration (défaut)
    ros2 launch musia_bringup navigation.launch.py slam:=False map:=/chemin/carte.yaml

  * exploration (slam:=True) : la carte se construit pendant que le robot roule
    (slam_toolbox) et on peut déjà lui donner une destination. Aucune carte préalable :
    c'est le mode du premier prototype.
  * carte (slam:=False) : carte enregistrée + AMCL. Pour un lieu déjà cartographié,
    avec des destinations stables d'une séance à l'autre.

Le robot n'a pas d'encodeur : l'odométrie vient du lidar (rf2o_laser_odometry), corrigée
en rotation par le gyroscope dans l'EKF. Nav2 envoie ses vitesses sur /cmd_vel_auto,
que twist_mux transmet aux moteurs si le pilote manuel ne les commande pas.
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
    nav2 = get_package_share_directory('nav2_bringup')
    return LaunchDescription([
        DeclareLaunchArgument('slam', default_value='True'),
        DeclareLaunchArgument('map', default_value=os.path.join(share, 'maps', 'famienkro.yaml')),
        DeclareLaunchArgument('nav2_params', default_value=os.path.join(share, 'config', 'nav2_params.yaml')),

        Node(package='rf2o_laser_odometry', executable='rf2o_laser_odometry_node', name='rf2o',
             output='screen', parameters=[{
                 'laser_scan_topic': '/scan', 'odom_topic': '/odom_rf2o', 'publish_tf': False,
                 'base_frame_id': 'base_footprint', 'odom_frame_id': 'odom',
                 'init_pose_from_topic': '', 'freq': 10.0}]),
        Node(package='robot_localization', executable='ekf_node', name='ekf_node', output='screen',
             parameters=[os.path.join(share, 'config', 'ekf.yaml')],
             remappings=[('odometry/filtered', '/odom')]),

        # bringup_launch de Nav2 : slam:=True lance slam_toolbox à la place de map_server + AMCL
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(nav2, 'launch', 'bringup_launch.py')),
            launch_arguments={'slam': LaunchConfiguration('slam'),
                              'map': LaunchConfiguration('map'),
                              'params_file': LaunchConfiguration('nav2_params'),
                              'use_sim_time': 'false', 'autostart': 'true'}.items()),
    ])
