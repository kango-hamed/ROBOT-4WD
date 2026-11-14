from setuptools import find_packages, setup

package_name = 'musia_tour'

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
    description='Visite guidée MUSIA',
    license='MIT',
    tests_require=['pytest'],
    entry_points={'console_scripts': ['tour_node = musia_tour.tour_node:main']},
)
