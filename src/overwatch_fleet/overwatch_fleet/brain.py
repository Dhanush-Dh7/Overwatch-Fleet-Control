import os
import re 
import time
import json
import threading
import queue
import rclpy
import subprocess
from std_msgs.msg import String
from rclpy.node import Node
from datetime import datetime
from typing import TypedDict, List, Optional, Tuple
from dotenv import load_dotenv
from simulation import VirtualFleet, PATROL_LOCATIONS, ALL_LOCATIONS, CHARGING_BAY

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import StateGraph, END

load_dotenv()

class FleetCommandNode(Node):
    def __init__(self):
        super().__init__('fleet_commander')
        self.publisher_ = self.create_publisher(String, '/fleet_command', 10)

    def send_nav_command(self, robot, loc):
        msg = String()
        msg.data = f"Navigate:{robot}:{loc}"
        self.publisher_.publish(msg)
        self.get_logger().info(f"Published: {msg.data}")
if not rclpy.ok():
    rclpy.init()

_command_node = rclpy.create_node('fleet_command_publisher')
_command_pub = _command_node.create_publisher(String, '/fleet_command', 10)

def send_ros_command(robot_name, target_loc):
    """Publishes directly through a persistent, already-connected ROS publisher —
    no subprocess spawn, no per-call discovery wait, guaranteed call-order delivery."""
    msg = String()
    msg.data = f"Navigate:{robot_name}:{target_loc}"
    _command_pub.publish(msg)
    print(f"🚀 Published: {msg.data}")
    
fleet_manager = VirtualFleet(on_relocate=send_ros_command)
fleet_manager.start_simulation()

WORK_DURATION = 5.0        # brief "doing the task" phase after real arrival
pending_missions = {}      # robot_name -> {"token", "report_holder", "task", "is_chaos"}

class MissionCompleteListener(Node):
    def __init__(self):
        super().__init__('mission_complete_listener')
        self.create_subscription(String, '/mission_complete', self._on_complete, 10)

    def _on_complete(self, msg):
        try:
            robot_name, nav_status = msg.data.split(':', 1)
        except ValueError:
            return

        unit = fleet_manager.robots.get(robot_name)
        if unit is not None:
            unit['moving'] = False
            kind = unit.pop('_nav_kind', None)
            target = unit.pop('_nav_target', None)
            if nav_status == "SUCCEEDED" and target:
                unit['location'] = target
                if kind == "charge":
                    unit['charging'] = True
                    unit['status'] = f"Charging ({unit['battery']}%)"
                elif kind == "patrol":
                    unit['status'] = "Patrolling"
            elif nav_status != "SUCCEEDED" and kind in ("patrol", "charge"):
                # This branch was previously unreachable for auto-patrol/auto-charge —
                # failures were silently swallowed, causing invisible retry loops.
                fleet_manager._log_event(
                    f"ALERT: {robot_name} auto-{kind} to {target or '?'} failed ({nav_status})"
                )
                unit['status'] = "Idle"
                unit['_completed_at'] = time.time()  # brief cooldown before the next auto-retry

        entry = pending_missions.pop(robot_name, None)
        if entry is None:
            return  # plain patrol/charge — already fully handled above

        if nav_status != "SUCCEEDED":
            if robot_name in fleet_manager.robots:
                fleet_manager.robots[robot_name]['status'] = 'Navigation Failed'
                fleet_manager.robots[robot_name]['mission'] = None
            fleet_manager._log_event(f"ALERT: {robot_name} navigation did not complete ({nav_status})")
            fleet_manager.complete_mission_board(
                robot_name,
                f"Mission interrupted ({nav_status}).",
                token=entry["token"]
            )
            return

        if robot_name in fleet_manager.robots:
            fleet_manager.robots[robot_name]['status'] = 'Executing Mission'
        if robot_name in fleet_manager.mission_board:
            fleet_manager.mission_board[robot_name]['enroute'] = False

        def _finish():
            report = entry["report_holder"][0] or f"{robot_name} completed: {entry['task']}. Mission accomplished."
            fleet_manager.complete_mission(robot_name)
            fleet_manager.complete_mission_board(robot_name, report, token=entry["token"])

        threading.Timer(WORK_DURATION, _finish).start()

def _spin_listener():
        consecutive_errors = 0
        while True:
            if not rclpy.ok():
                print("MissionCompleteListener: rclpy is shutting down, stopping listener thread.")
                return
            try:
                rclpy.spin(MissionCompleteListener())
            except Exception as e:
                consecutive_errors += 1
                print(f"⚠️ MissionCompleteListener crashed ({consecutive_errors}): {e}")
                if consecutive_errors >= 5:
                    print("⚠️ Too many consecutive crashes — giving up on MissionCompleteListener. "
                        "Mission status tracking is now broken until the app is restarted.")
                    return
                time.sleep(1)

threading.Thread(target=_spin_listener, daemon=True).start()        

class AgentState(TypedDict):
    messages: List[BaseMessage]
    reasoning_log: List[str]
    needs_approval: bool
    pending_action: dict
    mission_history: List[dict]
    
_llm = None
chaos_report_queue = queue.Queue()


def get_llm():
    global _llm
    if _llm is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        _llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            google_api_key=api_key,
            temperature=0.2,
        )
    return _llm


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        try:
            return json.loads(match.group().replace("'", '"'))
        except Exception:
            return None


def _build_fleet_context() -> str:
    lines = []
    for name, d in fleet_manager.robots.items():
        batt = d['battery']
        flags = []
        if batt < 25:
            flags.append("LOW BATTERY")
        if d['health'] != "Operational":
            flags.append(d['health'].upper())
        if d['charging']:
            flags.append("CHARGING")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"  {name} ({d['type']}): loc={d['location']}, battery={batt}%, "
            f"status={d['status']}{flag_str}"
        )
    return "\n".join(lines)


def _best_available_robot() -> str:
    available = fleet_manager.get_available_robots()
    if not available:
        candidates = [(n, d) for n, d in fleet_manager.robots.items() if d["health"] == "Operational"]
        if not candidates:
            return "Indra"
        return max(candidates, key=lambda x: x[1]["battery"])[0]
    return max(available, key=lambda n: fleet_manager.robots[n]["battery"])


def _resolve_robot(msg: str) -> Optional[str]:
    for r in fleet_manager.robots:
        if r.lower() in msg.lower():
            return r
    return None

# ── Strategist node ────────────────────────────────────────────────────────

def strategist_node(state: AgentState) -> AgentState:
    messages = state.get("messages", [])
    logs = state.get("reasoning_log", [])
    history = state.get("mission_history", [])

    if not messages:
        return {**state, "messages": messages + [AIMessage(content="No input received.")]}

    last_msg = messages[-1].content
    fleet_data = fleet_manager.robots

    # ── Greeting detection ────────────────────────────────────────────────────
    _GREETING_SET = {
        "hi", "hello", "hey", "howdy", "hiya", "yo", "sup", "greetings",
        "bonjour", "hola", "ciao", "namaste", "salut", "hallo", "ola", "hoi",
        "good morning", "good afternoon", "good evening", "what's up", "whats up",
    }
    _GREETING_REPLIES = {
        "hi": "Hi, Commander!",
        "hello": "Hello, Commander!",
        "hey": "Hey, Commander!",
        "howdy": "Howdy, Commander!",
        "hiya": "Hey there, Commander!",
        "yo": "Yo, Commander!",
        "sup": "Hey Commander!",
        "greetings": "Greetings, Commander!",
        "bonjour": "Bonjour, Commander!",
        "hola": "Hola, Commander!",
        "ciao": "Ciao, Commander!",
        "namaste": "Namaste, Commander!",
        "salut": "Salut, Commander!",
        "hallo": "Hallo, Commander!",
        "ola": "Ola, Commander!",
        "hoi": "Hoi, Commander!",
        "good morning": "Good morning, Commander!",
        "good afternoon": "Good afternoon, Commander!",
        "good evening": "Good evening, Commander!",
        "what's up": "Hey Commander!",
        "whats up": "Hey Commander!",
    }

    _stripped = last_msg.strip().lower().rstrip("!.,? ")
    _is_greeting = False
    _follow_up = ""
    _matched_greeting = ""

    # Fast path 1: exact match (pure greeting, nothing else)
    if _stripped in _GREETING_SET:
        _is_greeting = True
        _matched_greeting = _stripped

    # Fast path 2: starts with greeting word followed by a command
    if not _is_greeting:
        for _gw in sorted(_GREETING_SET, key=len, reverse=True):
            if (_stripped.startswith(_gw + " ") or
                    _stripped.startswith(_gw + ",") or
                    _stripped.startswith(_gw + "!")):
                _is_greeting = True
                _matched_greeting = _gw
                _follow_up = _stripped[len(_gw):].strip().lstrip(",!. ")
                break

    # LLM fallback: multilingual / unusual greetings not in the set
    if not _is_greeting and len(_stripped.split()) <= 4:
        _clf_prompt = (
            f'Is this a greeting or salutation in any language? Message: "{last_msg}"\n'
            f'Return ONLY JSON with these exact keys:\n'
            f'{{"greeting": false, "command": ""}}\n'
            f'Set greeting to true if the message is or starts with any hello/hi/salutation.\n'
            f'Set command to the non-greeting part, or empty string if none.'
        )
        try:
            _clf = _extract_json(get_llm().invoke(_clf_prompt).content) or {}
            _is_greeting = bool(_clf.get("greeting", False))
            _follow_up = (_clf.get("command") or "").strip()
            _matched_greeting = ""
        except Exception:
            _is_greeting = False
            _follow_up = ""
            _matched_greeting = ""

    # Build greeting prefix
    _greeting_prefix = ""
    if _is_greeting:
        _s = fleet_manager.get_fleet_summary()
        if _matched_greeting:
            _greeting_reply = _GREETING_REPLIES.get(_matched_greeting, "Hello, Commander!")
        else:
            try:
                _greeting_reply = get_llm().invoke(
                    f'Reply to this greeting: "{last_msg}"\n'
                    f'Respond with ONLY a short greeting back (3-6 words) in the SAME language '
                    f'as the input, addressing the person as "Commander". No extra text.'
                ).content.strip() or "Hello, Commander!"
            except Exception:
                _greeting_reply = "Hello, Commander!"
        _greeting_prefix = (
            f"{_greeting_reply} Overwatch HUD online — "
            f"{_s['operational']}/{_s['total']} units operational, "
            f"avg battery {_s['avg_battery']}%.\n\n"
        )

    # Pure greeting — no follow-up
    if _is_greeting and not _follow_up:
        return {
            "messages": messages + [AIMessage(content=_greeting_prefix + "Standing by for orders.")],
            "reasoning_log": logs + ["Strategist: Greeting acknowledged, no dispatch."],
            "needs_approval": False,
            "pending_action": {},
            "mission_history": history,
        }

    # Resolve effective message (strip greeting portion if present)
    _effective_msg = _follow_up if (_is_greeting and _follow_up) else last_msg
    _effective_lower = _effective_msg.lower()
    _effective_robot = _resolve_robot(_effective_lower)

    # ── Info requests ─────────────────────────────────────────────────────────
    info_keywords = [
        "status", "battery", "where", "location", "position", "health",
        "report", "vitals", "fleet", "all units", "summary", "patrol",
        "how is", "what is", "tell me about", "check on", "show me",
    ]
    is_info_request = any(kw in _effective_lower for kw in info_keywords)

    if is_info_request:
        if _effective_robot:
            unit = fleet_data[_effective_robot]
            batt_bar = "█" * (unit['battery'] // 10) + "░" * (10 - unit['battery'] // 10)
            if any(w in _effective_lower for w in ["where", "location", "position"]):
                res = f"Commander, **{_effective_robot}** is currently at **{unit['location']}**."
            elif "battery" in _effective_lower:
                res = f"**{_effective_robot}** battery: **{unit['battery']}%** `{batt_bar}`"
            else:
                res = (
                    f"**{_effective_robot}** ({unit['type']}) Status:\n"
                    f"- Location: {unit['location']}\n"
                    f"- Battery: {unit['battery']}% `{batt_bar}`\n"
                    f"- Status: {unit['status']}\n"
                    f"- Health: {unit['health']}\n"
                    f"- Active Mission: {unit['mission'] or 'None'}\n"
                    f"- Charging: {'Yes ⚡' if unit['charging'] else 'No'}"
                )
        else:
            summary = fleet_manager.get_fleet_summary()
            robot_lines = "\n".join([
                f"- **{n}** ({d['type']}): {d['location']} | {d['battery']}% | {d['status']}"
                for n, d in fleet_data.items()
            ])
            hazard_line = (
                f"\n\n⚠️ **Active Hazards:** {', '.join(fleet_manager.hazards)}"
                if fleet_manager.hazards else "\n\n✅ No active hazards."
            )
            res = (
                f"**Fleet Status Report**\n"
                f"Total: {summary['total']} | Operational: {summary['operational']} | "
                f"On Mission: {summary['on_mission']} | Charging: {summary['charging']} | "
                f"Avg Battery: {summary['avg_battery']}%\n\n"
                f"{robot_lines}{hazard_line}"
            )
        return {
            "messages": messages + [AIMessage(content=_greeting_prefix + res)],
            "reasoning_log": logs + [f"Strategist: Telemetry for {_effective_robot or 'fleet'}"],
            "needs_approval": False,
            "pending_action": {},
            "mission_history": history,
        }

    # ── Mission dispatch ───────────────────────────────────────────────────────
    is_hazard = any(
        h in _effective_lower for h in [
            "fire", "leak", "emergency", "breach", "critical",
            "spill", "collapse", "evacuate", "hazard"
        ]
    )

    prompt = f"""You are the Overwatch Strategist AI for a factory. Determine the best robot and action.

COMMANDER'S INTENT: "{_effective_msg}"

FLEET STATUS:
{_build_fleet_context()}

FACTORY ZONES: {', '.join(PATROL_LOCATIONS)}
ALL LOCATIONS: {', '.join(ALL_LOCATIONS)}

ACTIVE HAZARDS: {fleet_manager.hazards if fleet_manager.hazards else 'None'}

ROBOT ROLES:
- Support (Indra, Vayu, Trishul): General factory operations, any task
- Disaster Mgmt (Agni, Rudra): Emergency response, hazard containment, fire, spills

INSTRUCTIONS:
- For hazards/emergencies: prefer Agni or Rudra.
- For general work: use Indra, Vayu, or Trishul.
- Avoid robots with battery below 25% unless explicitly named.
- Return ONLY valid JSON:
{{"robot": "<robot_name>", "action": "<Move to LOCATION or task description>", "reasoning": "<one sentence>"}}"""

    try:
        raw = get_llm().invoke(prompt).content
        decision = _extract_json(raw)
        if not decision or "robot" not in decision:
            raise ValueError(f"Could not parse JSON: {raw[:200]}")
    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "quota" in err_str.lower() or "rate" in err_str.lower():
            decision = {
                "robot": _effective_robot or _best_available_robot(),
                "action": "Standing by — API rate limit reached, try again shortly",
                "reasoning": "rate_limited"
            }
        else:
            fallback = _effective_robot or _best_available_robot()
            decision = {"robot": fallback, "action": "General inspection", "reasoning": str(e)[:80]}

    chosen = decision.get("robot", "Indra")
    if chosen in fleet_data and fleet_data[chosen]["battery"] < 25:
        logs = logs + [f"Strategist: Warning — {chosen} battery low ({fleet_data[chosen]['battery']}%). Consider recharging."]

    return {
        "messages": messages,
        "reasoning_log": logs + [
            f"Strategist: Dispatch {decision['robot']} → {decision['action']} | {decision.get('reasoning', '')}"
        ],
        "needs_approval": is_hazard,
        "pending_action": decision,
        "mission_history": history,
    }
# ── Logistics node ─────────────────────────────────────────────────────────

def logistics_node(state: AgentState) -> AgentState:
    action = state.get("pending_action", {})
    robot = action.get("robot", _best_available_robot())
    task = action.get("action", "General Inspection")
    logs = state.get("reasoning_log", [])

    schedule_completion(robot, task, is_chaos=False, delay=7.0)
    result = f"Dispatching {robot}: {task}"

    history_entry = {"timestamp": str(datetime.now()), "robot": robot, "task": task, "result": result}
    return {
        "messages": state.get("messages", []) + [AIMessage(content=result)],
        "reasoning_log": logs + [f"Logistics: {task[:40]} — {result[:60]}"],
        "mission_history": state.get("mission_history", []) + [history_entry],
        "needs_approval": False,
        "pending_action": {},
    }

# ── Chaos response ─────────────────────────────────────────────────────────

def handle_chaos_event(event: str, response_loc: str) -> Tuple[str, List[str]]:
    available = fleet_manager.get_available_robots()
    if not available:
        msg = (f"⚠️ **FACTORY ALERT:** {event}\n\n**CRITICAL:** No available units.")
        chaos_report_queue.put((event, response_loc, [], []))
        return msg, []

    disaster_units = [r for r in available if fleet_manager.robots[r]["type"] == "Disaster Mgmt"]
    pool = disaster_units if disaster_units else available
    responders = sorted(pool, key=lambda n: fleet_manager.robots[n]["battery"], reverse=True)[:2]

    dispatch_results = []
    for robot in responders:
        task = f"Emergency response: {event} at {response_loc}"   # location name embedded
        schedule_completion(robot, task, is_chaos=True, delay=7.0)
        dispatch_results.append((robot, fleet_manager.robots[robot]["type"], f"Dispatched to {response_loc}"))

    responder_lines = "\n".join([f"  - **{n}** ({t}): {r}" for n, t, r in dispatch_results])
    brief_report = (f"🚨 **FACTORY ALERT:** {event}\n\n**Units Dispatched:**\n{responder_lines}")
    chaos_report_queue.put((event, response_loc, responders, dispatch_results))
    return brief_report, responders

def drain_chaos_report_queue() -> list:
    items = []
    while True:
        try:
            items.append(chaos_report_queue.get_nowait())
        except queue.Empty:
            break
    return items


# ── Completion report + scheduling ─────────────────────────────────────────

def generate_completion_report(robot: str, task: str, is_chaos: bool = False) -> str:
    unit = fleet_manager.robots.get(robot, {})
    location = unit.get("location", "unknown location")
    battery = unit.get("battery", 0)
    if is_chaos:
        return f"{robot} has contained the situation at {location}. Battery at {battery}%. Area secured, standing by for next order."
    return f"{robot} completed '{task}' and is now at {location} with {battery}% battery. Returning to standby."

# ── Feature: Post-incident executive summary ────────────────────────────────

def generate_incident_summary(cleared_hazards: list) -> str:
    """Generate a Gemini-powered post-incident summary after hazards are cleared."""
    if not cleared_hazards:
        return "All clear — no incidents were recorded for this session."

    recent = fleet_manager.mission_ledger[-12:] if fleet_manager.mission_ledger else []
    mission_summary = "\n".join(
        f"  - {m['time']} | {m['unit']}: {m['task']} [{m['status']}]"
        for m in recent
    ) or "  No missions logged."

    prompt = f"""You are the Overwatch AI writing an executive post-incident report for a factory.

INCIDENTS THAT OCCURRED:
{chr(10).join(f"- {h}" for h in cleared_hazards)}

RECENT MISSIONS EXECUTED:
{mission_summary}

Write a concise 1-2 paragraph executive summary covering:
1. What incidents occurred and which zones were affected
2. Which robot units responded and what actions were taken
3. Whether the situation was contained and the factory is safe
4. Overall operational status now that hazards are cleared

Use a professional factory operations tone. Be factual and concise."""

    try:
        return get_llm().invoke(prompt).content.strip()
    except Exception:
        return (
            f"{len(cleared_hazards)} incident(s) cleared: "
            + ", ".join(cleared_hazards)
            + ". Factory status nominal. All units standing by."
        )


# ── Feature: Multi-step mission planner ─────────────────────────────────────

MULTI_STEP_KEYWORDS = [
    "all zones", "all areas", "entire factory", "whole factory",
    "every zone", "all units", "deploy all", "patrol all",
    "inspect all", "secure all", "cover all", "check all",
    "full sweep", "all robots", "everyone to",
]


def plan_multi_step(command: str) -> list:
    """Returns list of {robot, task} dicts for factory-wide commands, or [] for single-dispatch."""
    cmd_lower = command.lower()
    if not any(kw in cmd_lower for kw in MULTI_STEP_KEYWORDS):
        return []

    available = fleet_manager.get_available_robots()
    if len(available) < 2:
        return []

    prompt = f"""You are planning a multi-robot factory-wide operation.

COMMANDER'S ORDER: "{command}"
AVAILABLE ROBOTS: {available}
FACTORY ZONES: {', '.join(PATROL_LOCATIONS)}
ALL LOCATIONS: {', '.join(ALL_LOCATIONS)}

Assign each available robot a specific, meaningful task or zone.
Return ONLY a valid JSON array — no extra text:
[{{"robot": "RobotName", "task": "specific task description"}}, ...]

Rules:
- Use every robot in the available list
- Each task must be different and zone-specific
- Tasks should align with robot type (Disaster Mgmt robots handle hazard/safety checks)"""

    try:
        raw = get_llm().invoke(prompt).content
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            plans = json.loads(match.group())
            valid = [p for p in plans if isinstance(p, dict) and "robot" in p and "task" in p]
            # Only return plans for robots that are actually available
            return [p for p in valid if p["robot"] in available]
    except Exception:
        pass
    return []

def schedule_completion(robot: str, task: str, is_chaos: bool = False, delay: float = 7.0):
    if robot not in fleet_manager.robots:
        return
    if fleet_manager.robots[robot]['health'] != 'Operational':
        fleet_manager._log_event(f"{robot} dispatch blocked — health: {fleet_manager.robots[robot]['health']}")
        return
    token = fleet_manager.add_to_mission_board(robot, task, is_chaos)
    report_holder = [None]

    def _generate():
        report_holder[0] = generate_completion_report(robot, task, is_chaos)
    threading.Thread(target=_generate, daemon=True).start()

    fleet_manager.robots[robot]['mission'] = task
    fleet_manager.robots[robot]['status'] = 'En Route'
    fleet_manager.mission_board[robot]['enroute'] = True

    target_loc = next((loc for loc in ALL_LOCATIONS if loc.lower() in task.lower()), None)

    if target_loc:
        fleet_manager.robots[robot]['moving'] = True
        fleet_manager.robots[robot]['_nav_kind'] = 'llm'
        fleet_manager.robots[robot]['_nav_target'] = target_loc
        pending_missions[robot] = {"token": token, "report_holder": report_holder, "task": task, "is_chaos": is_chaos}
        send_ros_command(robot, target_loc)

        def _fallback():
            entry_now = pending_missions.get(robot)
            if entry_now is None or entry_now.get("token") != token:
                return
            pending_missions.pop(robot, None)
            fleet_manager.robots[robot]['moving'] = False
            fleet_manager._log_event(f"ALERT: {robot} mission timed out waiting for /mission_complete — bridge_node may be unresponsive")
            report = report_holder[0] or f"{robot} completed: {task}. Mission accomplished."
            fleet_manager.complete_mission(robot)
            fleet_manager.complete_mission_board(robot, report, token=token)
        threading.Timer(90.0, _fallback).start()   # <-- fixed, generous ceiling — not tied to `delay`
    else:
        def _set_executing():
            if fleet_manager.robots.get(robot, {}).get('status') == 'En Route':
                fleet_manager.robots[robot]['status'] = 'Executing Mission'
            if robot in fleet_manager.mission_board:
                fleet_manager.mission_board[robot]['enroute'] = False
        threading.Timer(6.0, _set_executing).start()

        def _fire():
            report = report_holder[0] or f"{robot} completed: {task}. Mission accomplished."
            fleet_manager.complete_mission(robot)
            fleet_manager.complete_mission_board(robot, report, token=token)
        threading.Timer(delay, _fire).start()
# ── Router + graph ─────────────────────────────────────────────────────────

def router(state: AgentState) -> str:
    messages = state.get("messages", [])
    if messages and isinstance(messages[-1], AIMessage):
        return "end"
    if state.get("needs_approval"):
        return "human_review"
    return "execute"


workflow = StateGraph(AgentState)
workflow.add_node("strategist", strategist_node)
workflow.add_node("logistics", logistics_node)
workflow.set_entry_point("strategist")
workflow.add_conditional_edges("strategist", router, {
    "human_review": END,
    "execute": "logistics",
    "end": END,
})
workflow.add_edge("logistics", END)
agent_executor = workflow.compile()
