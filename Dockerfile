# Use the official ROS 2 Jazzy image
FROM ros:jazzy-ros-base

# Prevent interactive prompts during build
ENV DEBIAN_FRONTEND=noninteractive

# Install pip and venv
RUN apt-get update && apt-get install -y \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
# Update line 16 to this:
RUN pip3 install --break-system-packages --no-cache-dir --ignore-installed -r requirements.txt


# Copy all your project files
COPY . .

# Source the ROS environment and run your app
# The --server.port 8501 is the default for Streamlit
CMD ["/bin/bash", "-c", "source /opt/ros/jazzy/setup.bash && streamlit run src/overwatch_fleet/overwatch_fleet/app.py --server.port=8501 --server.address=0.0.0.0"]