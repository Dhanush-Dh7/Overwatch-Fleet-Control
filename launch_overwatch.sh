#!/bin/bash
source /opt/ros/jazzy/setup.bash
source ~/overwatch_ws/install/setup.bash
export PYTHONPATH=$PYTHONPATH:$(pwd)/src:$(pwd)/venv/lib/python3.12/site-packages
/usr/bin/python3 -m streamlit run src/overwatch_fleet/overwatch_fleet/app.py
