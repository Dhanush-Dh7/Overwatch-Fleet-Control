import random
from datetime import datetime

class VirtualFleet:
    def __init__(self):
        self.robots = {
            "Agni-01": {"location": "Dock-A", "battery": 85, "status": "Idle", "health": "Operational", "mission": None},
            "Prithvi-02": {"location": "Warehouse-B", "battery": 42, "status": "Idle", "health": "Operational", "mission": None},
            "Trishul-03": {"location": "Dock-C", "battery": 100, "status": "Idle", "health": "Operational", "mission": None}
        }
        self.hazards = []
        self.mission_ledger = []

    def get_robot_data(self, name):
        return self.robots.get(name, "Robot not found.")

    def set_location(self, name, loc):
        if name not in self.robots:
            return "Error: Unknown Unit."
        
        unit = self.robots[name]
        if unit["health"] == "Malfunction":
            return f"Failure: {name} hardware compromised. Movement inhibited."
        
        old_loc = unit["location"]
        drain = random.randint(5, 12)
        unit["location"] = loc
        unit["battery"] = max(0, unit["battery"] - drain)
        unit["status"] = f"Relocated to {loc}"
        
        if random.random() < 0.08:
            unit["health"] = "Malfunction"
            return f"Alert: {name} reached {loc} but experienced a motor stall."

        return f"Success: {name} moved from {old_loc} to {loc}."

    def assign_mission(self, name, task):
        if name not in self.robots:
            return "Unit Offline."
        
        unit = self.robots[name]
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        impact = 20 if "emergency" in task.lower() else 10
        unit["battery"] = max(0, unit["battery"] - impact)
        unit["status"] = "Executing Mission"
        unit["mission"] = task
        
        entry = {"time": timestamp, "unit": name, "task": task, "status": "Initiated"}
        self.mission_ledger.append(entry)
        
        return f"Protocol: {name} assigned to {task} at {timestamp}."

    def trigger_chaos_event(self):
        scenarios = [
            "Chemical leak in Sector 7",
            "Main Power Grid fluctuation",
            "Trishul-03 sensor misalignment",
            "High heat signature in Warehouse-A"
        ]
        event = random.choice(scenarios)
        self.hazards.append(event)
        
        if "Trishul-03" in event:
            self.robots["Trishul-03"]["health"] = "Maintenance Required"
            
        return event

    def clear_hazards(self):
        self.hazards = []
        return "Environment Restored."