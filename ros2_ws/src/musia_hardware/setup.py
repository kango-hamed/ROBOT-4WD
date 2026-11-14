from setuptools import find_packages, setup

package_name = 'musia_hardware'

setup(
    name=package_name,
    version='2.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    description='Servo de la tête, IMU MPU-6050 et INA219 de MUSIA',
    license='MIT',
    tests_require=['pytest'],
    entry_points={'console_scripts': [
        'head_node = musia_hardware.nodes:head_main',
        'imu_node = musia_hardware.nodes:imu_main',
        'battery_node = musia_hardware.nodes:battery_main',
    ]},
)
