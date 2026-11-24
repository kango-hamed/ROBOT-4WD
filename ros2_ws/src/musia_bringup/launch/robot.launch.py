"""Matériel du robot : TF (URDF), carte moteur, lidar, tête, IMU, batterie, webcam.

    ros2 launch musia_bringup robot.launch.py
    ros2 launch musia_bringup robot.launch.py lidar:=false head:=false   # matériel absent

Chaque élément se désactive séparément, pour tester le robot pièce par pièce.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory('musia_bringup')
    params = LaunchConfiguration('params_file')
    with open(os.path.join(share, 'urdf', 'musia.urdf'), encoding='utf-8') as f:
        urdf = f.read()

    def part(switch, **kw):  # « switch » et non « name » : name= est déjà passé au Node
        return Node(condition=IfCondition(LaunchConfiguration(switch)), output='screen',
                    parameters=[params], **kw)

    args = [DeclareLaunchArgument('params_file', default_value=os.path.join(share, 'config', 'robot.yaml'))]
    args += [DeclareLaunchArgument(n, default_value='true')
             for n in ('base', 'lidar', 'head', 'imu', 'battery', 'camera')]

    return LaunchDescription(args + [
        Node(package='robot_state_publisher', executable='robot_state_publisher',
             parameters=[{'robot_description': urdf}], output='screen'),
        part('base', package='musia_base', executable='base_node', name='base_node'),
        part('lidar', package='ldlidar_stl_ros2', executable='ldlidar_stl_ros2_node', name='ldlidar'),
        part('head', package='musia_hardware', executable='head_node', name='head_node'),
        part('imu', package='musia_hardware', executable='imu_node', name='imu_node'),
        part('battery', package='musia_hardware', executable='battery_node', name='battery_node'),
        part('camera', package='v4l2_camera', executable='v4l2_camera_node', name='camera',
             remappings=[('image_raw', '/camera/image_raw'), ('camera_info', '/camera/camera_info')]),
    ])
