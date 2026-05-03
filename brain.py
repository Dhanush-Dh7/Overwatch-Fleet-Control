import os
import re
import json
import threading
import queue
from datetime import datetime
from typing import TypedDict, List, Optional, Tuple
from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import StateGraph, END
from simulation import VirtualFleet, PATROL_LOCATIONS, ALL_LOCATIONS

load_dotenv()
fleet_manager = VirtualFleet()
fleet_manager.start_simulation()


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
    last_lower = last_msg.lower()
    fleet_data = fleet_manager.robots
    target_robot = _resolve_robot(last_lower)

    # ── Info requests: ALWAYS answer directly — never require approval ──────
    info_keywords = [
        "status", "battery", "where", "location", "position", "health",
        "report", "vitals", "fleet", "all units", "summary", "patrol",
        "how is", "what is", "tell me about", "check on", "show me",
    ]
    is_info_request = any(kw in last_lower for kw in info_keywords)

    if is_info_request:
        if target_robot:
            unit = fleet_data[target_robot]
            batt_bar = "█" * (unit['battery'] // 10) + "░" * (10 - unit['battery'] // 10)
            if any(w in last_lower for w in ["where", "location", "position"]):
                res = f"Commander, **{target_robot}** is currently at **{unit['location']}**."
            elif "battery" in last_lower:
                res = f"**{target_robot}** battery: **{unit['battery']}%** `{batt_bar}`"
            else:
                res = (
                    f"**{target_robot}** ({unit['type']}) Status:\n"
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
            "messages": messages + [AIMessage(content=res)],
            "reasoning_log": logs + [f"Strategist: Direct telemetry for {target_robot or 'fleet'}"],
            "needs_approval": False,
            "pending_action": {},
            "mission_history": history,
        }

    # ── Mission dispatch — only flag hazard when the message itself requests
    #    emergency action; do NOT block routine commands just because an old
    #    hazard is still sitting in the hazard list.
    is_hazard = any(
        h in last_lower for h in ["fire", "leak", "emergency", "breach", "critical", "spill", "collapse", "evacuate", "hazard"]
    )

    prompt = f"""You are the Overwatch Strategist AI for a factory. Determine the best robot and action.

COMMANDER'S INTENT: "{last_msg}"

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
        fallback = target_robot or _best_available_robot()
        decision = {"robot": fallback, "action": "General inspection", "reasoning": str(e)[:80]}

    chosen = decision.get("robot", "Titan")
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

    target_loc = next((loc for loc in ALL_LOCATIONS if loc.lower() in task.lower()), None)

    if target_loc or any(w in task.lower() for w in ["move", "relocate", "go to", "patrol to"]):
        destination = target_loc or "Assembly-A"
        result = fleet_manager.set_location(robot, destination)
        log_type = f"Relocation to {destination}"
    else:
        result = fleet_manager.assign_mission(robot, task)
        log_type = f"Mission: {task[:40]}"

    history_entry = {"timestamp": str(datetime.now()), "robot": robot, "task": task, "result": result}

    return {
        "messages": state.get("messages", []) + [AIMessage(content=result)],
        "reasoning_log": logs + [f"Logistics: {log_type} — {result[:60]}"],
        "mission_history": state.get("mission_history", []) + [history_entry],
        "needs_approval": False,
        "pending_action": {},
    }


# ── Chaos response ─────────────────────────────────────────────────────────

def handle_chaos_event(event: str, response_loc: str) -> Tuple[str, List[str]]:
    """Returns (report_text, dispatched_robot_names)."""
    available = fleet_manager.get_available_robots()

    if not available:
        msg = (
            f"⚠️ **FACTORY ALERT:** {event}\n\n"
            f"**CRITICAL:** No available units. All robots are offline, on mission, or charging."
        )
        return msg, []

    disaster_units = [r for r in available if fleet_manager.robots[r]["type"] == "Disaster Mgmt"]
    pool = disaster_units if disaster_units else available
    responders = sorted(pool, key=lambda n: fleet_manager.robots[n]["battery"], reverse=True)[:2]

    dispatch_results = []
    for robot in responders:
        res = fleet_manager.set_location(robot, response_loc)
        dispatch_results.append((robot, fleet_manager.robots[robot]["type"], res))
        fleet_manager.assign_mission(robot, f"Emergency response: {event}")

    responder_lines = "\n".join([
        f"  - **{name}** ({rtype}): {res}"
        for name, rtype, res in dispatch_results
    ])

    brief_report = (
        f"🚨 **FACTORY ALERT:** {event}\n\n"
        f"**Units Dispatched:**\n{responder_lines}\n\n"
        f"**Incident Report:** Emergency protocols engaged."
    )
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
    rtype = unit.get("type", "Unit")

    prompt = f"""You are the Overwatch AI for a factory sending a task completion report.

UNIT: {robot} ({rtype})
TASK COMPLETED: {task}
CURRENT LOCATION: {location}
BATTERY REMAINING: {battery}%
TASK TYPE: {"Emergency Response" if is_chaos else "Standard Task"}

Write a 2-3 sentence completion report:
1. Confirm the task is done and how it was handled
2. State the unit's current condition and location
3. {"Confirm the situation is contained and area is safe." if is_chaos else "State the unit is returning to standby."}

Professional factory ops tone. Be concise."""

    try:
        report = get_llm().invoke(prompt).content.strip()
    except Exception:
        report = f"{robot} completed: {task}. Now at {location} with {battery}% battery."

    return report


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


def schedule_completion(robot: str, task: str, is_chaos: bool = False, delay: float = 5.0):
    """Marks mission 'ongoing' immediately; background timer flips to 'complete' after delay."""
    fleet_manager.add_to_mission_board(robot, task, is_chaos)

    def _fire():
        report = generate_completion_report(robot, task, is_chaos)
        fleet_manager.complete_mission(robot)
        fleet_manager.complete_mission_board(robot, report)

    t = threading.Timer(delay, _fire)
    t.daemon = True
    t.start()


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
