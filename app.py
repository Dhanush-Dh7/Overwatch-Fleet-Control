import streamlit as st
import base64
import os
from audio_recorder_streamlit import audio_recorder
from gtts import gTTS
from brain import agent_executor, fleet_manager
from langchain_core.messages import HumanMessage, AIMessage
from google import genai
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Overwatch: Fleet Control", page_icon="🛡️", layout="wide")

st.markdown("""
    <style>
    .stChatInputContainer { padding-bottom: 20px; }
    .reasoning-box { background-color: #1e1e1e; padding: 10px; border-radius: 5px; border-left: 3px solid #6aa36f; }
    .stMain { padding-bottom: 120px; }
    </style>
    """, unsafe_allow_html=True)

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "reasoning_log" not in st.session_state:
    st.session_state.reasoning_log = []
if "mission_history" not in st.session_state:
    st.session_state.mission_history = []
if "pending_action" not in st.session_state:
    st.session_state.pending_action = None

def speak(text):
    try:
        tts = gTTS(text=text, lang='en')
        tts.save("reply.mp3")
        with open("reply.mp3", "rb") as f:
            data = base64.b64encode(f.read()).decode()
        st.markdown(f'<audio autoplay><source src="data:audio/mp3;base64,{data}"></audio>', unsafe_allow_html=True)
    except: pass

def get_transcript(audio_bytes):
    try:
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=["Transcribe audio exactly:", {"mime_type": "audio/wav", "data": audio_bytes}]
        )
        return response.text.strip()
    except: return None

with st.sidebar:
    st.title("🛰️ Fleet Command")
    if st.button("⚠️ Trigger Chaos Event", use_container_width=True):
        evt = fleet_manager.trigger_chaos_event()
        st.error(f"ALERT: {evt}")
    st.divider()
    tab1, tab2 = st.tabs(["Vitals", "History"])
    with tab1:
        for name, stats in fleet_manager.robots.items():
            st.markdown(f"**{name}** ({stats['health']})")
            st.progress(stats['battery'] / 100)
            st.caption(f"Loc: {stats['location']} | {stats['status']}")
    with tab2:
        for entry in fleet_manager.mission_ledger[::-1]:
            st.markdown(f"<small>{entry['time']} - {entry['unit']}<br>{entry['task']}</small>", unsafe_allow_html=True)
            st.divider()

st.title("🛡️ Overwatch HUD")

for message in st.session_state.chat_history:
    role = "user" if isinstance(message, HumanMessage) else "assistant"
    with st.chat_message(role):
        st.markdown(message.content)

if st.session_state.pending_action:
    action = st.session_state.pending_action
    st.warning(f"⚠️ **APPROVAL REQUIRED:** Dispatch {action.get('robot')} for {action.get('action')}?")
    c1, c2 = st.columns(2)
    if c1.button("✅ Authorize Mission", use_container_width=True):
        # Trigger logistics manually for the pending action
        robot = action.get("robot")
        task = action.get("action")
        res = fleet_manager.assign_mission(robot, task)
        st.session_state.chat_history.append(AIMessage(content=res))
        st.session_state.pending_action = None
        st.rerun()
    if c2.button("❌ Abort", use_container_width=True):
        st.session_state.pending_action = None
        st.rerun()

input_container = st.container()
with input_container:
    c1, c2 = st.columns([1, 15])
    with c1:
        audio_data = audio_recorder(text="", pause_threshold=2.0, neutral_color="#6aa36f", icon_size="2x")
    with c2:
        user_text = st.chat_input("Enter strategic intent...")

query = None
if audio_data:
    query = get_transcript(audio_data)
elif user_text:
    query = user_text

# Locate the section inside 'if query:' in your app.py and update it:

if query:
    st.session_state.chat_history.append(HumanMessage(content=query))
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        # COMMANDER: State must be fully defined here to prevent KeyErrors
        initial_state = {
            "messages": st.session_state.chat_history,
            "reasoning_log": st.session_state.get("reasoning_log", []),
            "needs_approval": False,
            "pending_action": {},
            "mission_history": st.session_state.get("mission_history", [])
        }
        
        try:
            result = agent_executor.invoke(initial_state)
            
            # Update Session state
            st.session_state.reasoning_log = result.get("reasoning_log", [])
            st.session_state.mission_history = result.get("mission_history", [])
            
            with st.expander("🔍 Black Box Telemetry"):
                for log in st.session_state.reasoning_log:
                    st.markdown(f"• {log}")
            
            if result.get("needs_approval"):
                st.session_state.pending_action = result["pending_action"]
                ans = "Commander, hazardous conditions detected. Mission requires your authorization."
            else:
                ans = result["messages"][-1].content
            
            st.markdown(ans)
            speak(ans)
            st.session_state.chat_history.append(AIMessage(content=ans))
            st.rerun()
            
        except Exception as e:
            st.error(f"HUD Failure: {str(e)}")