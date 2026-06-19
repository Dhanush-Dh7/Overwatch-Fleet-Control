from setuptools import find_packages, setup

package_name = 'overwatch_fleet'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/overwatch_fleet']),
        ('share/overwatch_fleet', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='dhanush',
    maintainer_email='dhanush@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    extras_require={
        'test': [
            'pytest',
        ],
    },
    entry_points={
        'console_scripts': ["bridge_node = overwatch_fleet.ros2_bridge:main"
        ],
    },
)
