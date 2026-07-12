# Overwatch: Fleet Control

<p align="center">
  <img src="path/to/your/demo.gif" alt="Overwatch Fleet Demo" width="700"/>
</p>

Overwatch: Fleet Control is an advanced orchestration system designed to transition industrial automation from rigid, scripted processes to intelligent, autonomous agentic workflows. By integrating a "Strategist Node" powered by Gemini 2.0 Flash, the system enables dynamic task assignment, real-time emergency response, and proactive fleet maintenance.

## 🛠 Tech Stack
| Category | Technology |
| :--- | :--- |
| **Robotics** | ROS 2 (Jazzy), Nav2 Stack, TF2, Gazebo Harmonic, Foxglove Studio |
| **AI & Logic** | Gemini 2.0 Flash, LangGraph, Python, NLP Workflows |
| **Infrastructure** | Docker, Docker Compose, Streamlit HUD, REST APIs |

## 🚀 Engineering Features & Capabilities
* **Full-Stack Orchestration:** Bridged high-level cognitive planning directly to low-level ROS 2 Nav2 Action Servers via a custom Python execution layer.
* **Namespaced TF Topology:** Engineered a multi-robot transform tree (`/{robot}/odom` $\rightarrow$ `/{robot}/base_link`) to prevent coordinate frame collisions.
* **Complex System Integration:** Containerized Gazebo physics, Nav2 stacks, and custom map servers into a single, deployable Docker environment optimized for headless execution.
* **Strict Parameter Management:** Solved complex lifecycle node and C++ parameter parsing constraints to deploy robust collision monitoring and autonomous docking
* **Autonomous Multi-Agent Fleet:** Deploys and manages a coordinated fleet of five units (Indra, Vayu, Trishul, Agni, and Rudra).
* **Agentic Strategist Node:** Interprets natural language inputs into logical task sequences, mission plans, and real-time emergency overrides.
* **Real-time Fleet HUD:** Custom Streamlit-based dashboard providing telemetry, unit vitals, mission progress, and safety authorization loops.

## 💡 The Problem
Traditional industrial automation relies on rigid, pre-programmed scripts, leading to:
* **Logic Failure:** Minor environmental changes cause system-wide stalls.
* **The Reaction Gap:** Human intervention is often too slow during critical emergencies.
* **Operational Blindness:** Robots function as isolated tools, leading to inefficient resource and battery management.

## 🚀 The Solution
Overwatch implements an Agentic AI Fleet Orchestration layer:
* **Strategist Node:** Interprets natural language commands to generate logical mission plans.
* **Reasoning-Based Execution:** Evaluates fleet status before assigning tasks.
* **Self-Healing Loop:** Autonomous power management ensures 24/7 operational readiness.
* **Chaos Resilience:** A priority-based override system that halts routine work to address hazards.

## 📸 Project Gallery
### User Interface (Streamlit HUD)
![HUD Preview](https://github.com/user-attachments/assets/91a5a2d3-614f-4e52-9bbd-e2324a4ae80a)

### System Architecture
<img width="650" height="1000" alt="Cognitive Core Execution" src="https://github.com/user-attachments/assets/69a4359e-59a6-43cf-9387-ad0d8bc40e0d" />

## 🚀 Setup Instructions
1. **Clone the repository:** `git clone https://github.com/Dhanush-Dh7/Overwatch-Fleet-Control.git`
2. **Set Secrets:** Create a `.env` file and add your `GOOGLE_API_KEY`.
3. **Deploy:** `docker compose up -d && docker compose exec overwatch bash -c "/app/launch_overwatch.sh"`
4. **Interface:** Open `http://localhost:8501` in your browser.

## 🛠️ Collaboration Rules
- **NEVER** push directly to the `main` branch.
- Create a new branch for your feature: `git checkout -b feature-yourname`.
- Submit a **Pull Request** for the lead developer to review.
