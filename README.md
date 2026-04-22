# AgentVM 

AgentVM provides a desktop automation environment for LLM agents to interact with virtual machines through GUI and text-based actions. It is designed to be flexible to mount any skills and tools to be used in one unified Virtual Machine.

## 🎉 News
- **[2026/04/21]**: The AgentVM repo is created. Code and Paper Coming soon!




## 🧩 AgentVM: Towards Agent-Native Computer

After setting up the Virtual Machine, here’s how AgentVM supports more advanced usage and customization.  Given a digital task, an agent typically follows a loop: observe the computer → take an action → the computer executes it → observe again, until the task is complete.

### Motivation
This raises a practical question: *What can the agent observe, and what actions can it take?*

A basic observation–action space is the current graphical user interface (GUI) plus atomic GUI actions (click, typing, hotkey, etc.)—similar to how humans interact with computers:
- Actions: click, typing, hotkey, etc.
- Observations: screenshots

However, a purely GUI-based interface is often limiting for LLM-based agents:
- Most LLMs are optimized for text. Forcing the agent to operate only through pixel-level GUI actions can be inefficient and brittle.
- A screenshot only captures what is visible on screen; additional machine state can provide richer context for better decisions.

### AgentVM's Approach
To address this, AgentVM provides a more comprehensive interface.

For actions:
- Atomic GUI actions: click, typing, hotkey, etc.
- Advanced GUI actions: higher-level controls that can perform common operations without interacting with individual pixels (e.g., `open_window` to open an app by name, `switch_window`, and more).
- Text-based actions: actions that take text input and return text output (e.g., a `file_reader` action returns file content as text instead of a screenshot).

For observations:
- Screenshot: the RGB image of the current screen
- Text-based observations: structured state and outputs from text-based actions
- Other modalities: video and audio can also be captured and used as observations
