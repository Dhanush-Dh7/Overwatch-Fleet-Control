import os
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, ExecuteProcess
from launch_ros.actions import Node
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare

# Indian mythological names for the fleet
ROBOTS = ["indra", "vayu", "trishul", "agni", "rudra"]
START_POSITIONS = {
    "indra":   ( 2.0,  3.0),
    "vayu":    (-2.0,  3.0),
    "trishul": ( 2.0, -3.0),
    "agni":    ( 0.0,  5.0),
    "rudra":   (-2.0, -3.0),
}

def generate_launch_description():
    ld = LaunchDescription()

    # Define package paths
    overwatch_pkg = FindPackageShare('overwatch_fleet')
    ros_gz_sim_pkg = FindPackageShare('ros_gz_sim')
    tb3_pkg = FindPackageShare('turtlebot3_description')

    # 1. Launch Gazebo Harmonic (Headless Server)
    # This uses the .sdf file from your shell script instead of the old .world file
    world_file = PathJoinSubstitution([overwatch_pkg, 'worlds', 'factory.sdf'])
    
    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([ros_gz_sim_pkg, 'launch', 'gz_sim.launch.py'])
        ),
        # -r = run on start, -s = server only (headless)
        launch_arguments={'gz_args': ['-r -s --headless-rendering ', world_file]}.items()
    )
    ld.add_action(gz_sim)

    # 2. Start the ROS-Gazebo Clock Bridge
    # Nav2 absolutely needs this to sync ROS time with Gazebo Harmonic time
    clock_bridge = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=['/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock'],
        output='screen'
    )
    ld.add_action(clock_bridge)

    # 3. Spawn each robot using Harmonic's 'create' tool
    for robot in ROBOTS:
        x, y = START_POSITIONS[robot]
        
        # Resolve the URDF path dynamically
        urdf_file = PathJoinSubstitution([tb3_pkg, 'urdf', 'turtlebot3_burger.urdf'])
        
        ld.add_action(Node(
            package='ros_gz_sim', 
            executable='create',
            arguments=[
                '-name', robot,
                '-file', urdf_file,
                '-x', str(x), 
                '-y', str(y),
                '-z', '0.1' # Spawns slightly above ground to prevent physics glitches
            ],
            output='screen'
        ))

    # 4. Launch your Overwatch Bridge Node
    # I changed executable to 'bridge_node' to match your setup.py entry points
    ld.add_action(Node(
        package='overwatch_fleet',
        executable='bridge_node',
        output='screen'
    ))

    # 5. Launch the Streamlit Dashboard
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

    return ld