import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from overwatch_fleet.simulation import VirtualFleet
from overwatch_interfaces.srv import GetRobotStatus
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient

def sync_robot_states(fleet_manager):
    # Initialize node if not already done
    if not rclpy.ok():
        rclpy.init()
    
    node = rclpy.create_node('fleet_sync_node')
    client = node.create_client(GetRobotStatus, '/get_robot_details')
    
    # Wait for the service to be available
    if not client.wait_for_service(timeout_sec=1.0):
        node.get_logger().warn("Service /get_robot_details not available")
        node.destroy_node()
        return

    for name in fleet_manager.robots.keys():
        req = GetRobotStatus.Request()
        req.name = name
        
        future = client.call_async(req)
        # Use a small timeout to keep the UI responsive
        rclpy.spin_until_future_complete(node, future, timeout_sec=0.5)
        
        if future.result():
            res = future.result()
            # Update the local state dictionary used by Streamlit
            fleet_manager.robots[name]['location'] = res.location
            fleet_manager.robots[name]['status'] = res.status
            fleet_manager.robots[name]['battery'] = res.battery
            
            # Map ROS status to your UI health system
            if res.status == 'Maintenance':
                fleet_manager.robots[name]['health'] = "Maintenance Required"
            elif res.status == 'Malfunction':
                fleet_manager.robots[name]['health'] = "Malfunction"
            else:
                fleet_manager.robots[name]['health'] = "Operational"
                
    node.destroy_node()

class OverwatchBridge(Node):
    def __init__(self):
        super().__init__('overwatch_bridge')
        # Initialize simulation engine
        self.fleet = VirtualFleet()
        self.fleet.start_simulation()
        
        # Publisher to announce status to other ROS 2 nodes
        self.publisher = self.create_publisher(String, '/fleet_status', 10)
        self.timer = self.create_timer(1.0, self.timer_callback)
        self.get_logger().info("Overwatch Bridge is online and publishing to /fleet_status.")

        # Subscriber (Phase 4 updates applied here)
        self.subscription = self.create_subscription(String, '/fleet_command', self.command_callback, 10)

        # Service
        self.srv = self.create_service(GetRobotStatus, 'get_robot_details', self.get_details_callback)
        self.get_logger().info("Service 'get_robot_details' is ready.")

        self.ZONE_COORDS = {
            "Assembly-A":      (2.0,  3.0),
            "Assembly-B":      (2.0, -3.0),
            "Storage-Bay":     (-2.0, 3.0),
            "Processing-Unit": (-2.0,-3.0),
            "Loading-Dock":    (0.0,  5.0),
            "Charging-Bay":    (0.0, -5.0),
            "Maintenance-Bay": (4.0,  0.0),
            "Control-Room":    (-4.0, 0.0),
            "Dispatch-Zone":   (0.0,  0.0),
        }
        self.nav_clients = {
            name: ActionClient(self, NavigateToPose, f'/{name.lower()}/navigate_to_pose')
            for name in self.fleet.robots
        }
        
    def dispatch_to_location(self, robot_name: str, target_loc: str):
        client = self.nav_clients.get(robot_name)
        if not client or not client.wait_for_server(timeout_sec=2.0):
            return
        x, y = self.ZONE_COORDS.get(target_loc, (0.0, 0.0))
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.orientation.w = 1.0
        self.fleet.robots[robot_name]['status'] = 'En Route'
        client.send_goal_async(
            goal,
            feedback_callback=lambda fb: self._nav_feedback(robot_name, fb)
        )
        
    def _nav_feedback(self, robot_name, feedback):
        self.fleet.robots[robot_name]['status'] = 'En Route'

    def command_callback(self, msg):
        try:
            # Phase 4 update: Handle Navigate:Robot:Location strings from app.py
            data = msg.data
            parts = data.split(':')
            
            if parts[0] == "Navigate" and len(parts) == 3:
                robot_name, target_loc = parts[1], parts[2]
                if robot_name in self.nav_clients:
                    self.dispatch_to_location(robot_name, target_loc)
                    self.get_logger().info(f"Command received: Navigating {robot_name} to {target_loc}")
                else:
                    self.get_logger().warn(f"Robot '{robot_name}' not recognized.")        
        except Exception as e:
            self.get_logger().error(f"Command Error: {e}")
        
    def timer_callback(self):
        # Cleaned up the debug prints so it doesn't spam your terminal
        msg = String()
        fleet_status = {}
        for name, robot_data in self.fleet.robots.items():
            fleet_status[name] = {
                "location": robot_data.get('location', 'Unknown'),
                "status": robot_data.get('status', 'Unknown'),
                "battery": robot_data.get('battery', 0)
            }

        msg.data = str(fleet_status)
        self.publisher.publish(msg)

    def get_details_callback(self, request, response):
        self.get_logger().info(f"Incoming request for robot: {request.name}")
        
        # Access the live simulation state directly
        if request.name in self.fleet.robots:
            robot = self.fleet.robots[request.name]
            response.location = str(robot.get('location', 'Unknown'))
            response.status = str(robot.get('status', 'Unknown'))
            response.battery = int(robot.get('battery', 0))
            self.get_logger().info(f"Returning live data for {request.name}")
        else:
            self.get_logger().warn(f"Robot {request.name} not found in fleet.")
            response.location = "Unknown"
            response.status = "Not Found"
            response.battery = 0
            
        return response
   
    
def main(args=None):
    rclpy.init(args=args)
    bridge = OverwatchBridge()
    rclpy.spin(bridge)
    bridge.destroy_node()
    rclpy.shutdown()