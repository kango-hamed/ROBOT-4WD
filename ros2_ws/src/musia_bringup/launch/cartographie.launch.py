"""Cartographier un nouveau lieu (SLAM), robot piloté à la main.

    ros2 launch musia_bringup cartographie.launch.py
    ros2 run teleop_twist_keyboard teleop_twist_keyboard          # dans un autre terminal
    ros2 run nav2_map_server map_saver_cli -f ~/cartes/mon_musee   # pour enregistrer

Puis repérer dans RViz la position de chaque œuvre et la reporter dans le fichier de visite.

Pour le prototype, prototype.launch.py fait la même chose (slam:=True par défaut) avec la
page de pilotage : ce fichier ne sert plus que pour une cartographie au clavier.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('musia_bringup')
    slam = get_package_share_directory('slam_toolbox')
    return LaunchDescription([
        IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(share, 'launch', 'robot.launch.py'))),
        Node(package='rf2o_laser_odometry', executable='rf2o_laser_odometry_node', name='rf2o',
             parameters=[{'laser_scan_topic': '/scan', 'odom_topic': '/odom_rf2o', 'publish_tf': False,
                          'base_frame_id': 'base_footprint', 'odom_frame_id': 'odom',
                          'init_pose_from_topic': '', 'freq': 10.0}]),
        Node(package='robot_localization', executable='ekf_node', name='ekf_node',
             parameters=[os.path.join(share, 'config', 'ekf.yaml')],
             remappings=[('odometry/filtered', '/odom')]),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(slam, 'launch', 'online_async_launch.py')),
            launch_arguments={'use_sim_time': 'false'}.items()),
    ])
