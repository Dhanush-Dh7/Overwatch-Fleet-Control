#!/bin/bash

# Exit immediately if any command returns a error status
set -e

# Source the ROS 2 environment setup files
source /opt/ros/jazzy/setup.bash
source /app/install/setup.bash

# 1. Start a virtual frame buffer (Xvfb) for headless Gazebo rendering
echo "Initializing virtual display buffer (Xvfb)..."
Xvfb :99 -screen 0 1024x768x24 &
export DISPLAY=:99
export LIBGL_ALWAYS_SOFTWARE=1

# 2. Hand over total control to your master ROS 2 launch file
# This fires Streamlit, Gazebo, your bridge nodes, and Nav2 out of the box!
echo "Spinning up full Overwatch framework via factory_sim.launch.py..."
ros2 launch overwatch_fleet factory_sim.launch.py