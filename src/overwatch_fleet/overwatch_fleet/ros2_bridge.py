import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from overwatch_fleet.simulation import VirtualFleet
from overwatch_interfaces.srv import GetRobotStatus

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

        # Subscriber
        self.subscription = self.create_subscription(String,'fleet_command',self.command_callback,10)

        # Service
        self.srv = self.create_service(GetRobotStatus, 'get_robot_details', self.get_details_callback)
        self.get_logger().info("Service 'get_robot_details' is ready.")

    def command_callback(self, msg):
        try:
            # Expecting format "Manual:Indra"
            if ":" not in msg.data:
                return
                
            action, robot_name = msg.data.split(':')
            
            if robot_name in self.fleet.robots:
                # Update the simulation state
                self.fleet.robots[robot_name]["manual_override"] = (action == "Manual")
                self.get_logger().info(f"SUCCESS: {robot_name} mode set to {action}")
            else:
                self.get_logger().warn(f"Robot '{robot_name}' not recognized.")
        except Exception as e:
            self.get_logger().error(f"Command Error: {e}")
        
    def timer_callback(self):
        print("DEBUG: Fleet size is:", len(self.fleet.robots), flush=True)
        print("DEBUG: Fleet keys are:", list(self.fleet.robots.keys()), flush=True)
        msg = String()
        # Instead of just casting the whole dict to str(), 
        # let's create a readable summary of all robots
        fleet_status = {}
        for name, robot_data in self.fleet.robots.items():
            # Check if robot_data is a dict (which it is, according to the error)
            # Use bracket notation to safely access the values
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