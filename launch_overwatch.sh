#!/bin/bash
# 1. Source ROS 2 and your workspace
source /opt/ros/jazzy/setup.bash
source ~/overwatch_ws/install/setup.bash

# 2. Launch your ROS 2 nodes in the background
# Replace 'overwatch_fleet' and 'your_launch_file.launch.py' with your actual names
ros2 launch overwatch_fleet your_launch_file.launch.py &

# 3. Launch Streamlit
export PYTHONPATH=$PYTHONPATH:$(pwd)/src:$(pwd)/venv/lib/python3.12/site-packages
python3 -m streamlit run src/overwatch_fleet/overwatch_fleet/app.py