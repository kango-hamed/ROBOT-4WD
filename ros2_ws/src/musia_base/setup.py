from setuptools import find_packages, setup

package_name = 'musia_base'

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
    description='Pont /cmd_vel vers la carte moteur Arduino de MUSIA',
    license='MIT',
    tests_require=['pytest'],
    entry_points={'console_scripts': ['base_node = musia_base.base_node:main']},
)
