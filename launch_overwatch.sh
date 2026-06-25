#!/bin/bash
source /opt/ros/jazzy/setup.bash
source /app/install/setup.bash

# Start virtual display for Gazebo Harmonic headless
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
export LIBGL_ALWAYS_SOFTWARE=1

# Streamlit in background
nohup streamlit run src/overwatch_fleet/overwatch_fleet/app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false > app.log 2>&1 &

# Gazebo Harmonic (gz sim, not gazebo)
nohup gz sim worlds/factory.sdf --headless-rendering -s > gz.log 2>&1 &

# ROS-Gazebo bridge
nohup ros2 run ros_gz_bridge parameter_bridge \
    /clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock \
    > bridge.log 2>&1 &

# Your overwatch bridge node
ros2 run overwatch_fleet bridge_node