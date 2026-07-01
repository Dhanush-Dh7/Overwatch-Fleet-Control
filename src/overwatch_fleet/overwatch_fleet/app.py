import streamlit as st
import csv
import io
from collections import defaultdict
from brain import (
    agent_executor, fleet_manager,
    handle_chaos_event, schedule_completion,
    generate_incident_summary, plan_multi_step,
    drain_chaos_report_queue, send_ros_command
)
from langchain_core.messages import HumanMessage, AIMessage
from dotenv import load_dotenv
load_dotenv()


st.set_page_config(page_title="Overwatch: Fleet Control", page_icon="🛡️", layout="wide")

# ── Session state ─────────────────────────────────────────────────────────
for key, default in [
    ("chat_history", []),
    ("reasoning_log", []),
    ("mission_history", []),
    ("pending_action", None),
    ("battery_alerted", set()),   # Feature: battery alert tracking
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── Feature 1: Proactive battery drain alerts ─────────────────────────────
BATTERY_ALERT_THRESHOLD = 20
alert_fired = False
for _name, _stats in fleet_manager.robots.items():
    # Alert when below threshold, operational, not already charging, not already alerted
    if (
        _stats["battery"] <= BATTERY_ALERT_THRESHOLD
        and _stats["health"] == "Operational"
        and not _stats["charging"]
        and _name not in st.session_state.battery_alerted
    ):
        st.session_state.battery_alerted.add(_name)
        _alert = (
            f"🪫 **LOW BATTERY ALERT — {_name}** is at **{_stats['battery']}%**. "
            f"Recommend recharging immediately or it will auto-route to Charging-Bay."
        )
        st.session_state.chat_history.append(AIMessage(content=_alert))
        alert_fired = True


# Reset alert flag once a robot is recharged above 50%
for _name, _stats in fleet_manager.robots.items():
    if _stats["battery"] > 50:
        st.session_state.battery_alerted.discard(_name)

if alert_fired:
    st.rerun()

def health_color(health):
    return {"Operational": "🟢", "Maintenance Required": "🟡", "Malfunction": "🔴"}.get(health, "⚪")

def battery_icon(pct):
    if pct > 60:
        return "🔋"
    elif pct > 25:
        return "🪫"
    else:
        return "❌"

def type_badge(rtype):
    return {"Support": "🔧", "Disaster Mgmt": "🚒"}.get(rtype, "🤖")

# ── Feature 2: CSV mission log export ────────────────────────────────────

def build_mission_csv() -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["time", "unit", "task", "status"])
    writer.writeheader()
    writer.writerows(fleet_manager.mission_ledger)
    return output.getvalue()


def recent_chat(limit: int = 8):
    return st.session_state.chat_history[-limit:]

# ── Mission board renderer ────────────────────────────────────────────────
@st.fragment(run_every=2)
def render_mission_board():
    board = fleet_manager.mission_board
    if not board:
        return

    completed_entries = [
        (r, m) for r, m in board.items()
        if m["status"] == "complete" and m.get("completed_at")
    ]
    latest_robots = set()
    if completed_entries:
        max_ts = max(m["completed_at"] for _, m in completed_entries)
        latest_robots = {
            r for r, m in completed_entries
            if max_ts - m["completed_at"] <= 2.0
        }

    has_ongoing = any(m["status"] == "ongoing" for m in board.values())

    cards = ""
    for robot, m in board.items():
        is_ongoing = m["status"] == "ongoing"
        is_latest = robot in latest_robots

        if is_latest:
            border = "2px solid #ffd700"
            bg = "#1c1a0a"
            name_color = "#ffd700"
            icon = "✅"
            latest_badge = (
                "<div style='font-size:9px;color:#ffd700;text-transform:uppercase;"
                "letter-spacing:1px;margin-bottom:5px;white-space:normal;'>★ Latest Complete</div>"
            )
        elif is_ongoing:
            border = "1px solid #555"
            bg = "#111827"
            name_color = "#f59e0b"
            icon = "🔄"
            latest_badge = ""
        else:
            border = "1px solid #2d3748"
            bg = "#0f172a"
            name_color = "#4ade80"
            icon = "✅"
            latest_badge = ""

        task_text = m["task"][:40] + ("…" if len(m["task"]) > 40 else "")

        if is_ongoing:
            is_enroute = m.get("enroute", False)
            if is_enroute:
                body_html = "<span style='color:#60a5fa;font-size:10px;white-space:normal;'>🚀 En route…</span>"
            else:
                body_html = "<span style='color:#9ca3af;font-size:10px;white-space:normal;'>⏳ Executing…</span>"
        else:
            raw = m.get("report") or ""
            snip = raw[:90] + ("…" if len(raw) > 90 else "")
            body_html = (
                f"<span style='color:#d1d5db;font-size:10px;line-height:1.5;"
                f"white-space:normal;display:block;'>{snip}</span>"
            )

        cards += f"""
        <div style="
            display:inline-block;
            vertical-align:top;
            min-width:185px;
            max-width:185px;
            width:185px;
            background:{bg};
            border:{border};
            border-radius:8px;
            padding:10px 12px;
            margin-right:10px;
            box-sizing:border-box;
            flex-shrink:0;
            overflow:hidden;
            white-space:normal;
            word-break:break-word;
            overflow-wrap:break-word;
        ">
            {latest_badge}
            <div style="color:{name_color};font-weight:700;font-size:13px;margin-bottom:3px;
                        white-space:normal;overflow:hidden;word-break:break-word;">
                {icon} {robot}
            </div>
            <div style="color:#9ca3af;font-size:11px;margin-bottom:6px;
                        white-space:normal;overflow:hidden;word-break:break-word;">
                {task_text}
            </div>
            <div style="white-space:normal;overflow:hidden;word-break:break-word;
                        overflow-wrap:break-word;">{body_html}</div>
            <div style="color:#4b5563;font-size:9px;margin-top:7px;">{m['ts']}</div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:transparent;">
<div style="
    overflow-x:auto;
    overflow-y:hidden;
    white-space:nowrap;
    display:flex;
    flex-wrap:nowrap;
    align-items:flex-start;
    padding:6px 2px 4px 2px;
    width:100%;
    box-sizing:border-box;
">
{cards}
</div>
</body></html>"""

    label = "📋 Mission Board" + (" — ⏳ in progress" if has_ongoing else "")
    with st.expander(label, expanded=has_ongoing):
        st.html(html)


# ── Sidebar ───────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🛰️ Fleet Command")

    col_a, col_b = st.columns(2)

    if col_a.button("⚠️ Chaos Event", use_container_width=True):
        event, response_loc = fleet_manager.trigger_chaos_event()
        report, dispatched = handle_chaos_event(event, response_loc)
        #st.session_state.chat_history.append(AIMessage(content=report))
        for robot in dispatched:
            schedule_completion(robot, f"Emergency response: {event}", is_chaos=True, delay=7.0)
        st.rerun()

    if col_b.button("🔋 Recharge All", use_container_width=True):
        st.success(fleet_manager.recharge_all())
        st.rerun()

    if fleet_manager.hazards:
        st.divider()
        for h in fleet_manager.hazards:
            st.error(f"⚠️ {h}")
        if st.button("✅ Clear All Hazards", use_container_width=True):
            # Feature 4: Post-incident summary after clearing
            cleared = fleet_manager.clear_hazards()
            summary = generate_incident_summary(cleared)
            st.session_state.chat_history.append(
                AIMessage(content=f"📋 **POST-INCIDENT EXECUTIVE SUMMARY**\n\n{summary}")
            )
            st.rerun()

    st.divider()

    # Feature 2: CSV export button
    if fleet_manager.mission_ledger:
        st.download_button(
            label="📥 Export Mission Log (CSV)",
            data=build_mission_csv(),
            file_name="overwatch_mission_log.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.divider()

    tab_vitals, tab_map, tab_log = st.tabs(["Vitals", "Map", "Log"])


    with tab_vitals:
        
        summary = fleet_manager.get_fleet_summary()
        c1, c2 = st.columns(2)
        c1.metric("Units", f"{summary['operational']}/{summary['total']}")
        c2.metric("Avg Battery", f"{summary['avg_battery']}%")
        m1, m2 = st.columns(2)
        m1.metric("On Mission", summary['on_mission'])
        m2.metric("Charging", summary['charging'])

        # Feature 3: Robot utilization chart
        counts = fleet_manager.mission_counts
        if any(v > 0 for v in counts.values()):
            st.divider()
            st.caption("📊 Mission Utilization")
            st.bar_chart(counts)

        st.divider()

        for name, stats in fleet_manager.robots.items():
            charge_tag = " ⚡" if stats['charging'] else ""
            badge = type_badge(stats['type'])

            st.markdown(f"**{health_color(stats['health'])} {name}** {badge} `{stats['type']}`{charge_tag}")

            st.progress(stats['battery'] / 100, text=f"{battery_icon(stats['battery'])} {stats['battery']}%")

            # Derive status from mission board so it stays in sync with the board display
            mb = fleet_manager.mission_board.get(name, {})
            if mb.get('status') == 'ongoing':
                display_status = '🚀 En Route' if mb.get('enroute') else '⚙️ Executing Mission'
            else:
                display_status = stats['status']

            st.caption(f"📍 {stats['location']} | {display_status}")
            if stats.get('mission'):
                st.caption(f"🎯 {stats['mission'][:45]}")
            st.divider()
            

    with tab_map:
        from simulation import ALL_LOCATIONS
        loc_map = defaultdict(list)
        for name, stats in fleet_manager.robots.items():
            label = f"{health_color(stats['health'])} {name}"
            if stats['charging']:
                label += " ⚡"
            loc_map[stats['location']].append(label)
        for loc in ALL_LOCATIONS:
            robots_here = loc_map.get(loc, [])
            count = len(robots_here)
            header = f"📍 {loc} ({count})" if count else f"◻️ {loc} — empty"
            with st.expander(header, expanded=count > 0):
                if robots_here:
                    for r in robots_here:
                        st.write(r)
                else:
                    st.caption("No units present")

    with tab_log:
        events = fleet_manager.event_log[::-1]
        if not events:
            st.caption("No events yet.")
        for entry in events[:30]:
            is_alert = "ALERT" in entry["event"] or "CHAOS" in entry["event"]
            st.caption(f"{'🚨' if is_alert else '📋'} `{entry['time']}` {entry['event']}")

# ── Main HUD ──────────────────────────────────────────────────────────────
for event, response_loc, responders, dispatch_results in drain_chaos_report_queue():
    if not dispatch_results:
        queued_report = (
            f"⚠️ **FACTORY ALERT:** {event}\n\n"
            f"**CRITICAL:** No available units to dispatch. All robots are currently on mission or offline."
        )
    else:
        responder_lines = "\n".join([
            f"  - **{name}** ({rtype}): {res}"
            for name, rtype, res in dispatch_results
        ])
        queued_report = (
            f"🚨 **FACTORY ALERT:** {event}\n\n"
            f"**Units Dispatched:**\n{responder_lines}\n\n"
            f"**Incident Report:**\nEmergency protocols engaged at {response_loc}."
        )
    st.session_state.chat_history.append(AIMessage(content=queued_report))
    
st.markdown("### 🛡️ Overwatch HUD")

chat_container = st.container(height=370, border=False)
with chat_container:
    for message in st.session_state.chat_history:
        role = "user" if isinstance(message, HumanMessage) else "assistant"
        with st.chat_message(role):
            st.markdown(message.content)

render_mission_board()


# ── Pending approval ───────────────────────────────────────────────────────
if st.session_state.pending_action:
    action = st.session_state.pending_action
    robot = action.get("robot", "Unknown")
    task_action = action.get("action", "Unknown")
    reasoning = action.get("reasoning", "")

    st.warning(f"⚠️ **AUTHORIZATION REQUIRED** — Dispatch **{robot}** → {task_action}")
    if reasoning:
        st.caption(f"Rationale: {reasoning}")

    c1, c2 = st.columns(2)
    if c1.button("✅ Authorize Mission", use_container_width=True):
        from simulation import ALL_LOCATIONS
        target_loc = next(
            (loc for loc in ALL_LOCATIONS if loc.lower() in task_action.lower()), None
        )
        if target_loc:
            fleet_manager.set_location(robot, target_loc)
            send_ros_command(robot, target_loc)
            
        res = fleet_manager.assign_mission(robot, task_action)
        st.session_state.chat_history.append(AIMessage(content=res))
        schedule_completion(robot, task_action, is_chaos=False, delay=7.0)
        st.session_state.pending_action = None
        st.rerun()
    if c2.button("❌ Abort", use_container_width=True):
        st.session_state.chat_history.append(
            AIMessage(content=f"Mission aborted by Commander. {robot} stands down.")
        )
        st.session_state.pending_action = None
        st.rerun()

# ── Chat input ────────────────────────────────────────────────────────────
user_text = st.chat_input("Enter strategic intent...")

if user_text:
    st.session_state.chat_history.append(HumanMessage(content=user_text))

    # Feature 5: Multi-step mission planning
    multi_plans = plan_multi_step(user_text)
    if multi_plans:
        dispatched_lines = []
        for plan in multi_plans:
            robot = plan["robot"]
            task = plan["task"]
            res = fleet_manager.assign_mission(robot, task)
            if "confirmed" in res.lower() or "protocol" in res.lower():
                schedule_completion(robot, task, is_chaos=False, delay=5.0)
                dispatched_lines.append(f"- **{robot}** → {task}")
            else:
                dispatched_lines.append(f"- **{robot}** → ⚠️ {res}")

        ans = (
            f"🏭 **Factory-Wide Deployment Initiated** — {len(multi_plans)} units dispatched:\n\n"
            + "\n".join(dispatched_lines)
            + "\n\nAll units are executing simultaneously. Completion reports will follow in ~5 seconds."
        )
        st.session_state.chat_history.append(AIMessage(content=ans))
        st.rerun()

    else:
        # Standard single-dispatch flow
        initial_state = {
            "messages": st.session_state.chat_history,
            "reasoning_log": st.session_state.reasoning_log,
            "needs_approval": False,
            "pending_action": {},
            "mission_history": st.session_state.mission_history,
        }

        try:
            result = agent_executor.invoke({
                "messages": recent_chat(8),
                "reasoning_log": st.session_state.reasoning_log[-20:],
                "needs_approval": False,
                "pending_action": {},
                "mission_history": st.session_state.mission_history[-20:],
            })
            st.session_state.reasoning_log = result.get("reasoning_log", [])[-40:]
            st.session_state.mission_history = result.get("mission_history", [])[-40:]

            if result.get("needs_approval"):
                st.session_state.pending_action = result["pending_action"]
                ans = "Commander, active hazards detected. Mission requires authorization before deployment."
            else:
                msgs = result.get("messages", [])
                ans = (
                    msgs[-1].content
                    if msgs and isinstance(msgs[-1], AIMessage)
                    else "Mission status unclear."
                )
                if fleet_manager.mission_ledger:
                    last = fleet_manager.mission_ledger[-1]
                    if last["status"] == "Initiated":
                        schedule_completion(last["unit"], last["task"], is_chaos=False, delay=5.0)

            st.session_state.chat_history.append(AIMessage(content=ans))

        except Exception as e:
            st.session_state.chat_history.append(AIMessage(content=f"HUD Failure: {str(e)}"))

        st.rerun() 
