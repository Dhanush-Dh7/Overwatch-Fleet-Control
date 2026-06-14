#!/bin/bash
# Source the ROS 2 workspace
source /opt/ros/jazzy/setup.bash
source /app/install/setup.bash

# Launch the Streamlit app
# We use 'nohup' so the app keeps running if the terminal disconnects
nohup streamlit run src/overwatch_fleet/overwatch_fleet/app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false > app.log 2>&1 &

# Launch your ROS 2 node(s)
ros2 run overwatch_fleet bridge_node