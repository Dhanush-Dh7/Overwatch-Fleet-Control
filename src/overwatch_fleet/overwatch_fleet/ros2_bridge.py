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
from visualization_msgs.msg import Marker, MarkerArray
from rclpy.qos import QoSProfile, DurabilityPolicy


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
        super().__init__('overwatch_bridge')
        self.fleet = shared_fleet
        self.fleet.start_simulation()
        self.mission_complete_pub = self.create_publisher(String, '/mission_complete', 10)

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
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.zone_label_pub = self.create_publisher(MarkerArray, '/zone_labels', qos)
        self._publish_zone_labels()

        self.nav_clients = {
            name: ActionClient(self, NavigateToPose, f'/{name.lower()}/navigate_to_pose')
            for name in self.fleet.robots
        }
        self.active_goals = {}
        self.goal_generation = {} 
        self.odom_subs = []
        self.odom_pubs = {}
        self.tf_pubs = {}

        robots = ["indra", "vayu", "trishul", "agni", "rudra"]
        for robot_id in robots:
            self.odom_pubs[robot_id] = self.create_publisher(Odometry, f'/{robot_id}/odom', 10)
            self.tf_pubs[robot_id] = self.create_publisher(TFMessage, f'/{robot_id}/tf', 10)
            sub = self.create_subscription(
                Odometry, f'/{robot_id}/odom_raw',
                lambda msg, rid=robot_id: self.odom_callback(msg, rid),
                10
            )
            self.odom_subs.append(sub)

    def _publish_zone_labels(self):
        arr = MarkerArray()
        for i, (name, (x, y)) in enumerate(self.ZONE_COORDS.items()):
            m = Marker()
            m.header.frame_id = 'map'
            m.header.stamp = self.get_clock().now().to_msg()
            m.ns = 'zone_labels'
            m.id = i
            m.type = Marker.TEXT_VIEW_FACING
            m.action = Marker.ADD
            m.pose.position.x = x
            m.pose.position.y = y
            m.pose.position.z = 1.0
            m.scale.z = 0.5
            m.color.r = 1.0; m.color.g = 1.0; m.color.b = 1.0; m.color.a = 1.0
            m.text = name
            arr.markers.append(m)
        self.zone_label_pub.publish(arr)
            
    def dispatch_to_location(self, robot_name: str, target_loc: str):
        client = self.nav_clients.get(robot_name)
        if not client:
            self.get_logger().error(f"No action client found for {robot_name}!")
            return
        if not client.wait_for_server(timeout_sec=2.0):
            self.get_logger().error(f"Nav2 Action Server for '{robot_name}' is NOT READY! Command dropped.")
            return

        # Claim this dispatch as the latest one for this robot immediately,
        # before any cancellation/async work happens.
        my_gen = self.goal_generation.get(robot_name, 0) + 1
        self.goal_generation[robot_name] = my_gen

        def _send_new_goal():
            if self.goal_generation.get(robot_name) != my_gen:
                return  # a newer dispatch call already superseded this one

            x, y = self.ZONE_COORDS.get(target_loc, (0.0, 0.0))
            goal = NavigateToPose.Goal()
            goal.pose.header.frame_id = 'map'
            goal.pose.header.stamp = self.get_clock().now().to_msg()
            goal.pose.pose.position.x = float(x)
            goal.pose.pose.position.y = float(y)
            goal.pose.pose.orientation.w = 1.0

            self.fleet.robots[robot_name]['status'] = 'En Route'
            future = client.send_goal_async(
                goal,
                feedback_callback=lambda fb: self._nav_feedback(robot_name, fb)
            )
            self.active_goals[robot_name] = future
            future.add_done_callback(lambda f, r=robot_name, g=my_gen: self._goal_response_callback(f, r, g))

        existing = self.active_goals.get(robot_name)
        if existing is not None and existing.done():
            goal_handle = existing.result()
            if goal_handle.accepted:
                cancel_future = goal_handle.cancel_goal_async()
                cancel_future.add_done_callback(lambda f: _send_new_goal())
                return

        _send_new_goal()
        
    def _goal_response_callback(self, future, robot_name, gen):
        if self.goal_generation.get(robot_name) != gen:
            return  # a newer dispatch has already superseded this one
        goal_handle = future.result()
        if not goal_handle.accepted:
            self.get_logger().error(f"❌ Goal for {robot_name} was REJECTED by Nav2.")
            msg = String(); msg.data = f"{robot_name}:REJECTED"
            self.mission_complete_pub.publish(msg)
            return
        self.get_logger().info(f"✅ Goal for {robot_name} ACCEPTED! Moving...")
        result_future = goal_handle.get_result_async()
        result_future.add_done_callback(lambda f, r=robot_name, g=gen: self._goal_result_callback(f, r, g))

    def _goal_result_callback(self, future, robot_name, gen):
        from action_msgs.msg import GoalStatus
        if self.goal_generation.get(robot_name) != gen:
            return  # stale result — the current dispatch is already a later one
        status = future.result().status
        if status == GoalStatus.STATUS_CANCELED:
            return
        outcome = "SUCCEEDED" if status == GoalStatus.STATUS_SUCCEEDED else "FAILED"
        msg = String(); msg.data = f"{robot_name}:{outcome}"
        self.mission_complete_pub.publish(msg)
        self.get_logger().info(f"🏁 Navigation result for {robot_name}: {outcome}")
        
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