# Overwatch: Fleet Control

> **Project Status:** Under Active Development.
> Core fleet logic and simulation are functional. ROS 2 hardware integration is currently being modularized in the `feature/state-management-refactor` branch and will be merged into `main` shortly.

Overwatch: Fleet Control is an advanced orchestration system designed to transition industrial automation from rigid, scripted processes to intelligent, autonomous agentic workflows. By integrating a "Strategist Node" powered by Gemini 2.0 Flash, the system enables dynamic task assignment, real-time emergency response, and proactive fleet maintenance.

## 💡 The Problem
Traditional industrial automation relies on rigid, pre-programmed scripts, leading to:
* **Logic Failure:** Minor environmental changes cause system-wide stalls.
* **The Reaction Gap:** Human intervention is often too slow during critical emergencies.
* **Operational Blindness:** Robots function as isolated tools, leading to inefficient resource and battery management.

## 🚀 The Solution
Overwatch implements an Agentic AI Fleet Orchestration layer:
* **Strategist Node:** Interprets natural language commands to generate logical mission plans.
* **Reasoning-Based Execution:** Evaluates fleet status (proximity, battery levels, capabilities) before assigning tasks.
* **Self-Healing Loop:** Autonomous power management ensures units remain in a state of 24/7 operational readiness.
* **Chaos Resilience:** A priority-based override system that halts routine work to address immediate hazards.

## 📸 Project Gallery


### User Interface (Streamlit HUD)
![HUD Preview](https://github.com/user-attachments/assets/91a5a2d3-614f-4e52-9bbd-e2324a4ae80a)

### System Architecture
![Logic Flow](https://github.com/user-attachments/assets/e866caa7-2edf-42be-a305-07a31cabfda8)

---

## 🛠 Tech Stack
* **AI Core:** Gemini 2.0 Flash (Strategist Node)
* **Logic & State Management:** Python & LangGraph
* **Communication:** JSON Telemetry (for real-time unit vitals)
* **Interface:** Streamlit (Custom Terminal HUD)

## 📊 Key Features
* **Autonomous Dispatch:** Command a fleet with natural language (e.g., "Secure the factory").
* **Real-time HUD:** Monitor unit vitals, location maps, and mission progress.
* **Mission Logging:** Records actions for predictive maintenance and performance analysis.
* **Safety Authorization:** Integrated request system for high-risk operations.

## 🚀 Setup Instructions
1. **Clone the repository:**
   `git clone <https://github.com/Dhanush-Dh7/Overwatch-Fleet-Control.git>`
2. **Create a Virtual Env:**
   `python -m venv venv`
3. **Activate it:**
   - Windows: `.\venv\Scripts\activate`
   - Mac/Linux: `source venv/bin/activate`
4. **Install Dependencies:**
   `pip install -r requirements.txt`
5. **Set Secrets:** Create a `.env` file and add your `GOOGLE_API_KEY`.

## 🛠️ Collaboration Rules
- **NEVER** push directly to the `main` branch.
- Create a new branch for your feature: `git checkout -b feature-yourname`
- Submit a **Pull Request** for the lead developer to review.
