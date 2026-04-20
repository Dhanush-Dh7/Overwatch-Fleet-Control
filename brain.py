import os
import json
from datetime import datetime
from typing import TypedDict, List
from dotenv import load_dotenv

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.graph import StateGraph, END
from simulation import VirtualFleet

load_dotenv()
fleet_manager = VirtualFleet()

class AgentState(TypedDict):
    messages: List[BaseMessage]
    reasoning_log: List[str]
    needs_approval: bool
    pending_action: dict
    mission_history: List[dict]

llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash", google_api_key=os.getenv("GOOGLE_API_KEY"))

def strategist_node(state: AgentState):
    messages = state.get("messages", [])
    logs = state.get("reasoning_log", [])
    history = state.get("mission_history", [])
    last_msg = messages[-1].content.lower()
    
    fleet_data = fleet_manager.robots
    robot_names = list(fleet_data.keys())
    
    target_robot = next((r for r in robot_names if r.lower() in last_msg or r.split('-')[0].lower() in last_msg), None)
    
    info_keywords = ["status", "battery", "where", "location", "position", "health"]
    is_info_request = any(kw in last_msg for kw in info_keywords)
    is_hazard = len(fleet_manager.hazards) > 0 or any(h in last_msg for h in ["fire", "leak", "emergency"])

    if is_info_request and not is_hazard:
        if target_robot:
            unit = fleet_data[target_robot]
            if any(w in last_msg for w in ["where", "location", "position"]):
                res = f"Commander, {target_robot} is currently stationed at **{unit['location']}**."
            else:
                res = f"Vitals for {target_robot}: Battery {unit['battery']}%, Location {unit['location']}, Status: {unit['status']}."
        else:
            res = "Fleet Report: " + ", ".join([f"{n} @ {d['location']} ({d['battery']}%)" for n, d in fleet_data.items()])

        return {
            "messages": messages + [AIMessage(content=res)],
            "reasoning_log": logs + [f"Strategist: Telemetry extracted for {target_robot if target_robot else 'Fleet'}"],
            "needs_approval": False,
            "pending_action": {},
            "mission_history": history
        }

    prompt = (
        f"You are Overwatch Strategist. Intent: '{last_msg}'. "
        f"Fleet Positions: { {n: d['location'] for n, d in fleet_data.items()} }. "
        f"Hazards: {fleet_manager.hazards}. "
        "Task: If user mentions a destination (Dock-A, Dock-C, Warehouse-B), put it in 'action'. "
        "Return ONLY JSON: {'robot': 'Agni-01', 'action': 'Move to Dock-C'}"
    )
    
    try:
        raw = llm.invoke(prompt).content
        clean_json = raw.replace("```json", "").replace("```", "").strip()
        decision = json.loads(clean_json.replace("'", "\""))
    except:
        decision = {"robot": target_robot if target_robot else "Agni-01", "action": "Manual Assessment"}

    return {
        "messages": messages,
        "reasoning_log": logs + [f"Strategist: Tactical dispatch identified for {decision['robot']}"],
        "needs_approval": is_hazard,
        "pending_action": decision,
        "mission_history": history
    }

def logistics_node(state: AgentState):
    action = state.get("pending_action", {})
    robot = action.get("robot", "Agni-01")
    task = action.get("action", "Maintenance")
    
    known_locations = ["Dock-A", "Dock-C", "Warehouse-B", "Sector 7", "Warehouse-A"]
    target_loc = next((loc for loc in known_locations if loc.lower() in task.lower()), None)
    
    if target_loc or "move" in task.lower():
        destination = target_loc if target_loc else "Warehouse-B"
        result = fleet_manager.set_location(robot, destination)
        log_type = f"Relocation to {destination}"
    else:
        result = fleet_manager.assign_mission(robot, task)
        log_type = "Task Assignment"
    
    history_entry = {"timestamp": str(datetime.now()), "robot": robot, "task": task, "result": result}
    
    return {
        "messages": state.get("messages", []) + [AIMessage(content=result)],
        "reasoning_log": state.get("reasoning_log", []) + [f"Logistics: {log_type} confirmed."],
        "mission_history": state.get("mission_history", []) + [history_entry],
        "needs_approval": False,
        "pending_action": {}
    }

def router(state: AgentState):
    if state.get("messages") and isinstance(state["messages"][-1], AIMessage):
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
    "end": END
})

workflow.add_edge("logistics", END)
agent_executor = workflow.compile()