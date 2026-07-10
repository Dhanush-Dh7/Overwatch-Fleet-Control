from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from overwatch_fleet.simulation import VirtualFleet
from overwatch_interfaces.srv import GetRobotStatus
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from tf2_msgs.msg import TFMessage

def sync_robot_states(fleet_manager):
    if not rclpy.ok():
        rclpy.init()
    
    node = rclpy.create_node('fleet_sync_node')
    client = node.create_client(GetRobotStatus, '/get_robot_details')
    
    if not client.wait_for_service(timeout_sec=1.0):
        node.get_logger().warn("Service /get_robot_details not available")
        node.destroy_node()
        return

    for name in fleet_manager.robots.keys():
        req = GetRobotStatus.Request()
        req.name = name
        
        future = client.call_async(req)
        rclpy.spin_until_future_complete(node, future, timeout_sec=0.5)
        
        if future.result():
            res = future.result()
            fleet_manager.robots[name]['location'] = res.location
            fleet_manager.robots[name]['status'] = res.status
            fleet_manager.robots[name]['battery'] = res.battery
            
            if res.status == 'Maintenance':
                fleet_manager.robots[name]['health'] = "Maintenance Required"
            elif res.status == 'Malfunction':
                fleet_manager.robots[name]['health'] = "Malfunction"
            else:
                fleet_manager.robots[name]['health'] = "Operational"
                
    node.destroy_node()

class OverwatchBridge(Node):
    def __init__(self, shared_fleet):
        # CRITICAL FIX: Removed the invalid 'parameters' keyword argument. 
        # The launch file already handles passing 'use_sim_time' to this node.
        super().__init__('overwatch_bridge')
        self.fleet = shared_fleet
        self.fleet.start_simulation()
        
        self.publisher = self.create_publisher(String, '/fleet_status', 10)
        self.timer = self.create_timer(1.0, self.timer_callback)
        self.get_logger().info("Overwatch Bridge is online and publishing to /fleet_status.")

        self.subscription = self.create_subscription(String, '/fleet_command', self.command_callback, 10)
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
        self.active_goals = {}
        # --- THE ODOM INTERCEPTOR ---
        self.odom_subs = []
        self.odom_pubs = {}
        self.tf_pubs = {}
        
        # Explicitly define the robots so subscriptions generate properly
        robots = ["indra", "vayu", "trishul", "agni", "rudra"]
        
        for robot_id in robots:
            self.odom_pubs[robot_id] = self.create_publisher(Odometry, f'/{robot_id}/odom', 10)
            self.tf_pubs[robot_id] = self.create_publisher(TFMessage, f'/{robot_id}/tf', 10)  # <-- new
            sub = self.create_subscription(
                Odometry, f'/{robot_id}/odom_raw',
                lambda msg, rid=robot_id: self.odom_callback(msg, rid),
                10
            )
            self.odom_subs.append(sub)
            
    def dispatch_to_location(self, robot_name: str, target_loc: str):
        client = self.nav_clients.get(robot_name)
        if not client:
            self.get_logger().error(f"No action client found for {robot_name}!")
            return
        if not client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error(f"Nav2 Action Server for '{robot_name}' is NOT READY! Command dropped.")
            return
            
        x, y = self.ZONE_COORDS.get(target_loc, (0.0, 0.0))
        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.orientation.w = 1.0
        
        self.fleet.robots[robot_name]['status'] = 'En Route'
        
        # Protect future, attach a result listener
        future = client.send_goal_async(
            goal,
            feedback_callback=lambda fb: self._nav_feedback(robot_name, fb)
        )
        self.active_goals[robot_name] = future 
        future.add_done_callback(lambda f, r=robot_name: self._goal_response_callback(f, r))
        
    def _goal_response_callback(self, future, robot_name):
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().error(f"❌ Goal for {robot_name} was REJECTED by Nav2.")
            return
        self.get_logger().info(f"✅ Goal for {robot_name} ACCEPTED! Moving...")
        
    def _nav_feedback(self, robot_name, feedback):
        self.fleet.robots[robot_name]['status'] = 'En Route'

    def odom_callback(self, msg, robot_id):
        # Print a message so we know the data is actually flowing
        # self.get_logger().info(f"Processing odometry for {robot_id}")
        
        t = TransformStamped()
        t.header.stamp = msg.header.stamp 
        t.header.frame_id = 'odom'        # bare, matches nav2_params.yaml
        t.child_frame_id = 'base_link'       
        
        t.transform.translation.x = msg.pose.pose.position.x
        t.transform.translation.y = msg.pose.pose.position.y
        t.transform.translation.z = msg.pose.pose.position.z
        t.transform.rotation = msg.pose.pose.orientation
        
        self.tf_pubs[robot_id].publish(TFMessage(transforms=[t]))

        msg.header.frame_id = f'{robot_id}/odom'
        msg.child_frame_id = f'{robot_id}/base_link'
        self.odom_pubs[robot_id].publish(msg)
        
    def command_callback(self, msg):
        try:
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
        if request.name in self.fleet.robots:
            robot = self.fleet.robots[request.name]
            response.location = str(robot.get('location', 'Unknown'))
            response.status = str(robot.get('status', 'Unknown'))
            response.battery = int(robot.get('battery', 0))
        else:
            response.location = "Unknown"
            response.status = "Not Found"
            response.battery = 0
            
        return response
   
    
def main(args=None):
    rclpy.init(args=args)
    # Provided a default VirtualFleet instance in case it is run standalone
    bridge = OverwatchBridge(VirtualFleet())
    rclpy.spin(bridge)
    bridge.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()