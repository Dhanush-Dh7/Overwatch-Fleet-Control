FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3-pip \
    # Gazebo Harmonic + ROS2 bridge (Jazzy uses gz, not gazebo)
    ros-jazzy-ros-gz \
    ros-jazzy-gz-ros2-control \
    # Nav2
    ros-jazzy-navigation2 \
    ros-jazzy-nav2-bringup \
    ros-jazzy-nav2-msgs \
    # TurtleBot3 (Jazzy build)
    ros-jazzy-turtlebot3 \
    ros-jazzy-turtlebot3-simulations \
    # Headless display for Gazebo in Docker
    xvfb \
    mesa-utils \
    && rm -rf /var/lib/apt/lists/*

ENV TURTLEBOT3_MODEL=burger
# Headless display
ENV DISPLAY=:99
ENV LIBGL_ALWAYS_SOFTWARE=1

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages --no-cache-dir --ignore-installed -r requirements.txt
COPY . .

CMD ["/bin/bash", "-c", \
    "Xvfb :99 -screen 0 1024x768x24 & \
     source /opt/ros/jazzy/setup.bash && \
     streamlit run src/overwatch_fleet/overwatch_fleet/app.py \
     --server.port=8501 --server.address=0.0.0.0"]