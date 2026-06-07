import random
import threading 
import time
from datetime import datetime

PATROL_LOCATIONS = ["Assembly-A", "Assembly-B", "Storage-Bay", "Processing-Unit", "Loading-Dock"]
CHARGING_BAY = "Charging-Bay"
ALL_LOCATIONS = PATROL_LOCATIONS + [CHARGING_BAY, "Maintenance-Bay", "Control-Room", "Dispatch-Zone"]
LOCATIONS = ALL_LOCATIONS

LOW_BATTERY_THRESHOLD = 25
FULL_BATTERY_THRESHOLD = 90


class VirtualFleet:
    def __init__(self):
        # 3 Support (general), 2 Disaster Management — no repair bot
        self.robots = {
            "Indra":  {"location": "Assembly-A",     "battery": 85, "status": "Idle", "health": "Operational", "mission": None, "type": "Support",       "charging": False,"manual_override": False},
            "Vayu":   {"location": "Storage-Bay",    "battery": 72, "status": "Idle", "health": "Operational", "mission": None, "type": "Support",       "charging": False,"manual_override": False},
            "Trishul":{"location": "Assembly-B",     "battery": 60, "status": "Idle", "health": "Operational", "mission": None, "type": "Support",       "charging": False,"manual_override": False},
            "Agni":   {"location": "Loading-Dock",   "battery": 90, "status": "Idle", "health": "Operational", "mission": None, "type": "Disaster Mgmt", "charging": False,"manual_override": False},
            "Rudra":  {"location": "Processing-Unit","battery": 55, "status": "Idle", "health": "Operational", "mission": None, "type": "Disaster Mgmt", "charging": False,"manual_override": False},
        }
        self.hazards = []
        self.mission_ledger = []
        self.event_log = []
        self._lock = threading.Lock()
        self._sim_running = False
        self._sim_thread = None

        # Mission board: robot_name -> {task, status, report, ts, is_chaos, completed_at}
        self.mission_board = {}
        self._pending_timer_count = 0

        # Feature: per-robot mission completion counter for utilization chart
        self.mission_counts = {name: 0 for name in self.robots}

    # ── Mission board ──────────────────────────────────────────────────────

    def add_to_mission_board(self, robot: str, task: str, is_chaos: bool = False):
        self.mission_board[robot] = {
            "task": task,
            "status": "ongoing",
            "report": None,
            "ts": datetime.now().strftime("%H:%M:%S"),
            "is_chaos": is_chaos,
            "completed_at": None,
        }
        self._pending_timer_count += 1

    def complete_mission_board(self, robot: str, report: str):
        if robot in self.mission_board:
            self.mission_board[robot]["status"] = "complete"
            self.mission_board[robot]["report"] = report
            self.mission_board[robot]["completed_at"] = datetime.now().timestamp()
        self._pending_timer_count = max(0, self._pending_timer_count - 1)

    # ── Background simulation ──────────────────────────────────────────────

    def start_simulation(self):
        if self._sim_running:
            return
        self._sim_running = True
        self._sim_thread = threading.Thread(target=self._sim_loop, daemon=True)
        self._sim_thread.start()

    def stop_simulation(self):
        self._sim_running = False

    def _sim_loop(self):
        while self._sim_running:
            time.sleep(20)
            try:
                self.simulation_tick()
            except Exception:
                pass

    def simulation_tick(self):
        with self._lock:
            for name, unit in self.robots.items():
                if unit.get("manual_override", False):
                    continue
                if unit["health"] == "Malfunction" or unit["status"] == "Offline":
                    continue

                if unit["charging"]:
                    gain = random.randint(15, 25)
                    unit["battery"] = min(100, unit["battery"] + gain)
                    unit["status"] = f"Charging ({unit['battery']}%)"
                    if unit["battery"] >= FULL_BATTERY_THRESHOLD:
                        unit["charging"] = False
                        unit["status"] = "Idle"
                        self._log_event(f"{name} fully charged, resuming patrol")
                    continue

                if unit["status"] == "Executing Mission":
                    drain = random.randint(2, 5)
                    unit["battery"] = max(0, unit["battery"] - drain)
                    if unit["battery"] == 0:
                        unit["status"] = "Battery Depleted"
                        unit["health"] = "Maintenance Required"
                        self._log_event(f"ALERT: {name} battery depleted mid-mission")
                    continue

                if unit["battery"] < LOW_BATTERY_THRESHOLD:
                    if unit["location"] != CHARGING_BAY:
                        unit["location"] = CHARGING_BAY
                        unit["status"] = f"Charging ({unit['battery']}%)"
                        unit["charging"] = True
                        self._log_event(f"{name} battery low ({unit['battery']}%), routing to Charging-Bay")
                    continue

                current = unit["location"]
                options = [loc for loc in PATROL_LOCATIONS if loc != current]
                if options:
                    new_loc = random.choice(options)
                    drain = random.randint(3, 8)
                    unit["battery"] = max(0, unit["battery"] - drain)
                    unit["location"] = new_loc
                    unit["status"] = "Patrolling"
                    self._log_event(f"{name} patrolling: {current} → {new_loc} (battery {unit['battery']}%)")
                    if random.random() < 0.03:
                        unit["health"] = "Maintenance Required"
                        unit["status"] = "Offline"
                        self._log_event(f"ALERT: {name} went offline at {new_loc}")

    # ── Fleet helpers ──────────────────────────────────────────────────────

    def get_robot_data(self, name):
        return self.robots.get(name, None)

    def get_fleet_summary(self):
        total = len(self.robots)
        operational = sum(1 for r in self.robots.values() if r["health"] == "Operational")
        avg_battery = sum(r["battery"] for r in self.robots.values()) // total
        on_mission = sum(1 for r in self.robots.values() if r["status"] == "Executing Mission")
        charging = sum(1 for r in self.robots.values() if r["charging"])
        return {
            "total": total,
            "operational": operational,
            "avg_battery": avg_battery,
            "on_mission": on_mission,
            "charging": charging,
            "hazard_count": len(self.hazards),
        }

    def get_available_robots(self):
        return [
            name for name, d in self.robots.items()
            if d["health"] == "Operational"
            and d["battery"] >= LOW_BATTERY_THRESHOLD
            and not d["charging"]
            and d["status"] != "Executing Mission"
        ]

    # ── Actions ────────────────────────────────────────────────────────────

    def set_location(self, name, loc):
        if name not in self.robots:
            return f"Error: Unknown unit '{name}'."
        unit = self.robots[name]
        if unit["health"] == "Malfunction":
            return f"Failure: {name} hardware compromised. Movement inhibited."
        if unit["battery"] < 15:
            return f"Warning: {name} battery critically low ({unit['battery']}%). Recharge before deployment."
        old_loc = unit["location"]
        if old_loc == loc:
            return f"{name} is already at {loc}. No movement required."
        drain = random.randint(5, 12)
        unit["location"] = loc
        unit["battery"] = max(0, unit["battery"] - drain)
        unit["status"] = f"Relocated to {loc}"
        unit["charging"] = False
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.mission_ledger.append({"time": timestamp, "unit": name, "task": f"Relocate to {loc}", "status": "Complete"})
        self._log_event(f"{name} relocated from {old_loc} to {loc}")
        if random.random() < 0.06:
            unit["health"] = "Malfunction"
            return f"Alert: {name} reached {loc} but experienced a hardware fault. Maintenance required."
        return f"Success: {name} moved from {old_loc} to {loc}. Battery: {unit['battery']}%."

    def assign_mission(self, name, task):
        if name not in self.robots:
            return f"Error: Unknown unit '{name}'."
        unit = self.robots[name]
        if unit["health"] in ("Malfunction", "Maintenance Required"):
            return f"Error: {name} is offline ({unit['health']}). Cannot assign mission."
        if unit["battery"] < 10:
            return f"Warning: {name} battery at {unit['battery']}%. Cannot execute mission safely."
        timestamp = datetime.now().strftime("%H:%M:%S")
        impact = 20 if "emergency" in task.lower() else 10
        unit["battery"] = max(0, unit["battery"] - impact)
        self.robots[name]["manual_override"] = True 
        self.robots[name]["mission"] = task
        self.robots[name]["status"] = "Executing Mission"
        unit["charging"] = False
        self.mission_ledger.append({"time": timestamp, "unit": name, "task": task, "status": "Initiated"})
        self._log_event(f"{name} assigned: {task}")
        return f"Protocol confirmed: {name} assigned to '{task}' at {timestamp}."

    def complete_mission(self, name):
        if name not in self.robots:
            return
        unit = self.robots[name]
        prev_task = unit.get("mission", "unknown task")
        self.robots[name]["manual_override"] = False
        self.robots[name]["mission"] = None
        self.robots[name]["status"] = "Idle"
        
        self.mission_counts[name] = self.mission_counts.get(name, 0) + 1
        self._log_event(f"{name} completed mission: {prev_task}")

    def recharge_robot(self, name):
        if name not in self.robots:
            return f"Error: Unknown unit '{name}'."
        unit = self.robots[name]
        old_battery = unit["battery"]
        unit["battery"] = 100
        unit["location"] = CHARGING_BAY
        unit["status"] = "Recharged"
        unit["charging"] = False
        if unit["health"] in ("Malfunction", "Maintenance Required"):
            unit["health"] = "Operational"
        self._log_event(f"{name} recharged: {old_battery}% → 100%")
        return f"{name} fully recharged (was {old_battery}%). Health restored."

    def recharge_all(self):
        for name in self.robots:
            self.recharge_robot(name)
        return "All units recharged to 100% and health restored."

    def trigger_chaos_event(self):
        scenarios = [
            ("Chemical spill at Assembly-A",             "Assembly-A",      None,    "Assembly-A"),
            ("Conveyor belt failure at Assembly-B",      "Assembly-B",      None,    "Assembly-B"),
            ("Fire alarm triggered at Storage-Bay",      "Storage-Bay",     None,    "Storage-Bay"),
            ("Power surge at Processing-Unit",           "Processing-Unit", None,    "Processing-Unit"),
            ("Vayu motor malfunction",                    None,              "Vayu",   "Maintenance-Bay"),
            ("Gas leak at Loading-Dock",                 "Loading-Dock",    None,     "Loading-Dock"),
            ("Rudra sensor failure",                      None,              "Rudra",  "Maintenance-Bay"),
            ("Coolant leak at Processing-Unit",          "Processing-Unit", None,     "Processing-Unit"),
            ("Equipment jam at Assembly-B",              "Assembly-B",      None,     "Assembly-B"),
            ("Structural vibration at Storage-Bay",      "Storage-Bay",     None,     "Storage-Bay"),
            ("Hydraulic failure at Loading-Dock",        "Loading-Dock",    None,     "Loading-Dock"),
            ("Indra power loss mid-patrol",               None,              "Indra",  "Charging-Bay"),
        ]
        scenario, affected_loc, affected_robot, response_loc = random.choice(scenarios)
        self.hazards.append(scenario)
        if affected_robot and affected_robot in self.robots:
            self.robots[affected_robot]["health"] = "Maintenance Required"
            self.robots[affected_robot]["status"] = "Offline"
        self._log_event(f"CHAOS EVENT: {scenario}")
        return scenario, response_loc

    def clear_hazards(self):
        """Returns the list of cleared hazards (for post-incident summary)."""
        cleared = list(self.hazards)
        self.hazards = []
        self._log_event(f"All hazards cleared by Commander ({len(cleared)} event(s))")
        return cleared

    def _log_event(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.event_log.append({"time": ts, "event": msg})
        if len(self.event_log) > 200:
            self.event_log = self.event_log[-200:]
