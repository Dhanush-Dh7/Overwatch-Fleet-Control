import os
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, ExecuteProcess
from launch_ros.actions import Node
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare
from launch.actions import TimerAction
from launch.actions import GroupAction
from launch_ros.actions import PushROSNamespace


# ROBOTS = ["indra", "vayu", "trishul", "agni", "rudra"]
ROBOTS = ["agni"]
START_POSITIONS = {
    "indra":   ( 2.0,  3.0),
    "vayu":    (-2.0,  3.0),
    "trishul": ( 2.0, -3.0),
    "agni":    ( 0.0,  5.0),
    "rudra":   (-2.0, -3.0),
}

def generate_launch_description():
    ld = LaunchDescription()

    overwatch_pkg = FindPackageShare('overwatch_fleet')
    ros_gz_sim_pkg = FindPackageShare('ros_gz_sim')

    world_file = PathJoinSubstitution([overwatch_pkg, 'worlds', 'factory.sdf'])
    
    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([ros_gz_sim_pkg, 'launch', 'gz_sim.launch.py'])
        ),
        launch_arguments={
            'gz_args': ['-r -s --headless-rendering ', world_file],
            'use_sim_time': 'True'
        }.items()
    )
    ld.add_action(gz_sim)

    clock_bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=['/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock'],
        parameters=[{'use_sim_time': True}],
        output='screen'
    )
    ld.add_action(clock_bridge)

    for robot in ROBOTS:
        x, y = START_POSITIONS[robot]
        URDF_FILE = '/app/src/overwatch_fleet/models/overwatch_agent.urdf'

        ld.add_action(Node(
            package='ros_gz_sim', executable='create',
            arguments=['-name', robot, '-file', URDF_FILE, '-x', str(x), '-y', str(y), '-z', '0.1'],
            output='screen'
        ))

        ld.add_action(TimerAction(
            period=5.0,
            actions=[
                GroupAction(actions=[
                    PushROSNamespace(robot),

                    # Nav2 stack
                    IncludeLaunchDescription(
                        PythonLaunchDescriptionSource(
                            PathJoinSubstitution([FindPackageShare('nav2_bringup'), 'launch', 'navigation_launch.py'])
                        ),
                        launch_arguments={
                            'namespace': robot,
                            'use_sim_time': 'True',
                            'params_file': os.path.join('/app/src/overwatch_fleet/config', 'nav2_params.yaml'),
                            'autostart': 'True',
                            'use_composition': 'False',
                        }.items()
                    ),

                    # map -> odom
                    Node(
                        package='tf2_ros', executable='static_transform_publisher',
                        name=f'map_to_{robot}_odom',
                        arguments=[
                            '--x', str(x), '--y', str(y), '--z', '0',
                            '--qx', '0', '--qy', '0', '--qz', '0', '--qw', '1',
                            '--frame-id', 'map', '--child-frame-id', 'odom'
                        ],
                        parameters=[{'use_sim_time': True}],
                        remappings=[('/tf', 'tf'), ('/tf_static', 'tf_static')],
                        output='screen'
                    ),

                    # base_link -> lidar_link
                    Node(
                        package='tf2_ros', executable='static_transform_publisher',
                        name='static_tf_lidar_' + robot,
                        arguments=[
                            '--x', '0', '--y', '0', '--z', '0',
                            '--qx', '0', '--qy', '0', '--qz', '0', '--qw', '1',
                            '--frame-id', 'base_link', '--child-frame-id', f'{robot}/lidar_link/lidar'
                        ],
                        parameters=[{'use_sim_time': True}],
                        remappings=[('/tf', 'tf'), ('/tf_static', 'tf_static')],
                        output='screen'
                    ),
                ])
            ]
        ))
    
        robot_bridge = Node(
            package='ros_gz_bridge',
            executable='parameter_bridge',
            name=f'bridge_{robot}',
            arguments=[
                f'/world/factory/model/{robot}/link/lidar_link/sensor/lidar/scan@sensor_msgs/msg/LaserScan[gz.msgs.LaserScan',
                f'/model/{robot}/odometry@nav_msgs/msg/Odometry[gz.msgs.Odometry',
                f'/model/{robot}/cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist',
                
            ],
            remappings=[
                (f'/world/factory/model/{robot}/link/lidar_link/sensor/lidar/scan', f'/{robot}/scan'),
                (f'/model/{robot}/odometry', f'/{robot}/odom_raw'), # <-- Routes to our interceptor
                (f'/model/{robot}/cmd_vel', f'/{robot}/cmd_vel'),
            ],
            output='screen'
        )
    ld.add_action(robot_bridge)
    
    ld.add_action(Node(
    package='nav2_map_server',
    executable='map_server',
    name='map_server',
    output='screen',
    parameters=[{
        'yaml_filename': PathJoinSubstitution([FindPackageShare('overwatch_fleet'), 'maps', 'factory_map.yaml']),
        'use_sim_time': True
    }]
))

    ld.add_action(Node(
        package='nav2_lifecycle_manager',
        executable='lifecycle_manager',
        name='lifecycle_manager_map',
        output='screen',
        parameters=[{
            'autostart': True,
            'node_names': [
                'map_server'
            ]
        }]
    ))    
        
    ld.add_action(Node(
        package='overwatch_fleet',
        executable='bridge_node',
        parameters=[{'use_sim_time': True}],
        output='screen'
    ))

    streamlit_dashboard = ExecuteProcess(
        cmd=[
            'streamlit', 'run', 'src/overwatch_fleet/overwatch_fleet/app.py',
            '--server.port=8501',
            '--server.address=0.0.0.0',
            '--server.headless=true',
            '--server.enableCORS=false',
            '--server.enableXsrfProtection=false'
        ],
        output='screen'
    )
    ld.add_action(streamlit_dashboard)

    ld.add_action(Node(
        package='foxglove_bridge',
        executable='foxglove_bridge',
        parameters=[{'port': 8765, 'use_sim_time': True}],
        output='screen'
    ))

    return ld