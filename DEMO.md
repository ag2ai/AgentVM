# AgentVM Demos

This document provides detailed examples of using the AgentVM environment.

## Demo 1: Basic Environment Usage

### Instantiation and Simple Interaction

```python
from agentvm.desktop_env import DesktopEnv

# Initialize environment
env = DesktopEnv(
    provider_name="docker",
    path_to_vm="docker_vm_data/Ubuntu.qcow2",
    os_type="Ubuntu",
    headless=False,
    require_a11y_tree=True,
    require_terminal=False,
)

print(f"Environment initialized")
print(f"Provider: {env.provider_name}")
print(f"Screen size: {env.screen_width}x{env.screen_height}")

# Reset environment
obs = env.reset()

print(f"Screenshot available: {obs['screenshot'] is not None}")
print(f"Accessibility tree available: {obs['accessibility_tree'] is not None}")
print(f"Available actions: {len(obs['action_space'])}")

# Execute a GUI action
action = {"action_type": "CLICK", "arguments": {"x": 500, "y": 300}}
obs, reward, done, info = env.step(action)

# Execute a keyboard action
action = {"action_type": "TYPING", "arguments": {"text": "Hello AgentVM"}}
obs, reward, done, info = env.step(action)

# Cleanup
env.close()
```

### With VNC Access (Docker)

```python
env = DesktopEnv(provider_name="docker", os_type="Ubuntu", headless=False)
obs = env.reset()

if hasattr(env, 'vnc_port'):
    print(f"VNC Web Access: http://localhost:{env.vnc_port}")
    print("Open this URL in your browser to see the desktop")

# Environment is now accessible via web browser
# Execute actions and see them in real-time

env.close()
```

---

## Demo 2: Action Bundle Registration and Usage

### Registering Actions

```python
from agentvm.desktop_env import DesktopEnv

env = DesktopEnv(provider_name="docker", os_type="Ubuntu")

# Define setup config with action registration
setup_config = [
    {
        "type": "execute",
        "parameters": {
            "command": ["python", "-c", "import pyautogui; pyautogui.click(960, 540);"]
        }
    },
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": "./actions/execute_bash"},
                {"local_path": "./actions/file_reader"},
                {"local_path": "./actions/str_replace_editor"},
                {"local_path": "./actions/text_web_browser"},
            ]
        }
    }
]

# Reset with setup
obs = env.reset(setup_config=setup_config)

print(f"Registered action bundles: {len(env.action_bundles)}")
for bundle in env.action_bundles:
    print(f"  - {bundle.name}: {len(bundle.commands)} commands")
```

### Using Registered Actions

```python
# Execute bash command
action = {
    "action_type": "execute_bash",
    "arguments": {"script": "pwd && ls -la"}
}
obs, reward, done, info = env.step(action)

if 'action_output' in obs:
    print(f"Status: {obs['action_output']['status']}")
    print(f"Output: {obs['action_output']['output']}")

# Read a file
action = {
    "action_type": "file_reader",
    "arguments": {"path": "/home/user/.bashrc"}
}
obs, reward, done, info = env.step(action)

if 'action_output' in obs:
    print(f"File contents: {obs['action_output']['output'][:200]}...")

# Create/edit a file
action = {
    "action_type": "str_replace_editor",
    "arguments": {
        "command": "create",
        "path": "/home/user/test.txt",
        "file_text": "Hello from AgentVM"
    }
}
obs, reward, done, info = env.step(action)

# Web search
action = {
    "action_type": "search",
    "arguments": {"query": "Python documentation"}
}
obs, reward, done, info = env.step(action)

env.close()
```

---

## Demo 3: Multi-Agent Interaction Pattern

### Hierarchical Agent System

This demonstrates the pattern used in `gdpagent/run_mm_task.py`:

```python
from agentvm.desktop_env import DesktopEnv
from pathlib import Path

REPO_ROOT = Path(".")
ACTIONS_DIR = REPO_ROOT / "actions"

# Initialize environment
env = DesktopEnv(
    provider_name="docker",
    path_to_vm="docker_vm_data/Ubuntu.qcow2",
    os_type="Ubuntu",
    headless=False,
)

# Register multiple action bundles for different agent capabilities
setup_config = [
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": str(ACTIONS_DIR / "execute_bash")},
                {"local_path": str(ACTIONS_DIR / "file_reader")},
                {"local_path": str(ACTIONS_DIR / "run_python")},
                {"local_path": str(ACTIONS_DIR / "str_replace_editor")},
                {"local_path": str(ACTIONS_DIR / "text_web_browser")},
            ]
        }
    }
]

obs = env.reset(setup_config=setup_config)

print(f"Environment ready with {len(obs['action_space'])} actions")
```

### Simulating Agent Task Execution

```python
# Task: Create a Python script and execute it

# Step 1: Bash Agent - Check current directory
print("[Controller -> Bash Agent] Check environment")
action = {
    "action_type": "execute_bash",
    "arguments": {"script": "pwd && whoami"}
}
obs, reward, done, info = env.step(action)
print(f"Result: {obs['action_output']['output']}")

# Step 2: File Agent - Create Python script
print("[Controller -> File Agent] Create script")
script_content = """#!/usr/bin/env python3
import platform
print(f"OS: {platform.system()}")
print(f"Release: {platform.release()}")
"""

action = {
    "action_type": "str_replace_editor",
    "arguments": {
        "command": "create",
        "path": "/home/user/system_info.py",
        "file_text": script_content
    }
}
obs, reward, done, info = env.step(action)
print(f"Result: {obs['action_output']['status']}")

# Step 3: Bash Agent - Execute script
print("[Controller -> Bash Agent] Run script")
action = {
    "action_type": "execute_bash",
    "arguments": {"script": "python3 /home/user/system_info.py"}
}
obs, reward, done, info = env.step(action)
print(f"Script output:\n{obs['action_output']['output']}")

# Step 4: File Agent - Verify file
print("[Controller -> File Agent] Verify file")
action = {
    "action_type": "file_reader",
    "arguments": {"path": "/home/user/system_info.py"}
}
obs, reward, done, info = env.step(action)
print(f"File verified: {len(obs['action_output']['output'])} bytes")

env.close()
```

---

## Demo 4: Environment Views

### Creating Restricted Views

```python
from agentvm.desktop_env import DesktopEnv

env = DesktopEnv(provider_name="docker", os_type="Ubuntu")

setup_config = [
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": "./actions/execute_bash"},
                {"local_path": "./actions/file_reader"},
                {"local_path": "./actions/text_web_browser"},
            ]
        }
    }
]

env.reset(setup_config=setup_config)

# Create a view with only bash and file operations
view = env.create_view(
    actions=["execute_bash", "file_reader"],
    name="bash_file_view"
)

print(f"Main env actions: {len(env.get_action_space())}")
print(f"View actions: {len(view.get_action_space())}")

# Use the view (only allowed actions work)
action = {"action_type": "execute_bash", "arguments": {"script": "ls -la"}}
obs, reward, done, info = view.step(action)

# This would fail in the view (action not allowed):
# action = {"action_type": "search", "arguments": {"query": "test"}}
# obs, reward, done, info = view.step(action)  # Error

view.close()
env.close()
```

### App-Specific Views

```python
# Open an application and create a view bound to its window
calc_view = env.create_app_view(app_name="mcp_libreoffice_calc")

# Actions are scoped to the app
# Screenshot shows only the app window
action = {
    "action_type": "insert_data",
    "arguments": {"cell": "A1", "value": "100"}
}
obs, reward, done, info = calc_view.step(action)

calc_view.close()
```

---

## Demo 5: Real-World Task Example

### Task: Install and Configure Software

```python
from agentvm.desktop_env import DesktopEnv

env = DesktopEnv(provider_name="docker", os_type="Ubuntu", headless=False)

setup_config = [
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": "./actions/execute_bash"},
                {"local_path": "./actions/file_reader"},
                {"local_path": "./actions/str_replace_editor"},
            ]
        }
    }
]

obs = env.reset(setup_config=setup_config)

# Step 1: Update package list
action = {
    "action_type": "execute_bash",
    "arguments": {"script": "sudo apt-get update"}
}
obs, reward, done, info = env.step(action)

# Step 2: Install package
action = {
    "action_type": "execute_bash",
    "arguments": {"script": "sudo apt-get install -y vim"}
}
obs, reward, done, info = env.step(action)

# Step 3: Verify installation
action = {
    "action_type": "execute_bash",
    "arguments": {"script": "which vim"}
}
obs, reward, done, info = env.step(action)
print(f"Vim installed at: {obs['action_output']['output']}")

# Step 4: Create configuration file
vimrc_content = """set number
set autoindent
syntax on
"""

action = {
    "action_type": "str_replace_editor",
    "arguments": {
        "command": "create",
        "path": "/home/user/.vimrc",
        "file_text": vimrc_content
    }
}
obs, reward, done, info = env.step(action)

# Step 5: Verify configuration
action = {
    "action_type": "file_reader",
    "arguments": {"path": "/home/user/.vimrc"}
}
obs, reward, done, info = env.step(action)
print(f"Configuration created:\n{obs['action_output']['output']}")

env.close()
```

---

## Demo 6: Observation Space Exploration

### Understanding Observations

```python
from agentvm.desktop_env import DesktopEnv
import numpy as np

env = DesktopEnv(
    provider_name="docker",
    os_type="Ubuntu",
    require_a11y_tree=True,
    require_terminal=False,
)

obs = env.reset()

# Screenshot
if obs['screenshot'] is not None:
    print(f"Screenshot shape: {obs['screenshot'].shape}")
    print(f"Screenshot dtype: {obs['screenshot'].dtype}")
    print(f"Screenshot size: {obs['screenshot'].nbytes / 1024 / 1024:.2f} MB")

# Accessibility tree
if obs['accessibility_tree'] is not None:
    print(f"Accessibility tree type: {type(obs['accessibility_tree'])}")
    # Tree contains UI element information

# State
print(f"State keys: {obs['state'].keys()}")

# Action space
print(f"Total actions: {len(obs['action_space'])}")
print(f"Sample actions: {obs['action_space'][:10]}")

# Execute action and check action_output
setup_config = [
    {
        "type": "register_action",
        "parameters": {
            "actions": [{"local_path": "./actions/execute_bash"}]
        }
    }
]

env.reset(setup_config=setup_config)

action = {
    "action_type": "execute_bash",
    "arguments": {"script": "echo 'test'"}
}
obs, reward, done, info = env.step(action)

# Action output (only for registered actions)
if 'action_output' in obs:
    print(f"Action output keys: {obs['action_output'].keys()}")
    print(f"Status: {obs['action_output']['status']}")
    print(f"Output: {obs['action_output']['output']}")
    print(f"Return code: {obs['action_output']['returncode']}")

env.close()
```

---

## Demo 7: Running from `live_quickstart.py`

The `live_quickstart.py` script provides an interactive demo:

```python
from agentvm.desktop_env import DesktopEnv

setup_config = [
    {
        "type": "execute",
        "parameters": {
            "command": ["python", "-c", "import pyautogui; pyautogui.click(960, 540);"]
        }
    },
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": "./actions/mcp_libreoffice_calc"}
            ]
        }
    }
]

env = DesktopEnv(
    provider_name="docker",
    path_to_vm="docker_vm_data/Ubuntu.qcow2",
    os_type="Ubuntu",
    headless=False,
)

obs = env.reset(setup_config=setup_config)

# Interactive loop - user enters bash commands
while True:
    command = input("$ ")

    if command.lower() == 'quit':
        break

    action_dict = {
        "action_type": "run_bash_script",
        "arguments": {"script": command}
    }

    obs, reward, done, info = env.step(action_dict)

    if 'action_output' in obs:
        for k, v in obs['action_output'].items():
            print(f"{k}: {v}")

env.close()
```

Run it: `python live_quickstart.py`

---

## Demo 8: GDP Agent Integration

From `gdpagent/run_mm_task.py`, showing how agents use the environment:

```python
from agentvm.desktop_env import DesktopEnv
from gdpagent.agents.cua_agent import CUAAgent

# Initialize environment
env = DesktopEnv(
    provider_name="docker",
    path_to_vm="docker_vm_data/Ubuntu.qcow2",
    os_type="Ubuntu",
    headless=False,
)

# Setup with action bundles
setup_config = [
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": "./actions/execute_bash"},
                {"local_path": "./actions/file_reader"},
                {"local_path": "./actions/text_web_browser"},
            ]
        }
    }
]

env.reset(setup_config=setup_config)

# Create CUA agent for GUI interactions
cua_agent = CUAAgent(
    max_steps=25,
    save_path="./task_run",
    client_password="password",
    env=env,
)

# Execute task
result = cua_agent.run(task="Complete the specified task")

env.close()
```

---

## Summary

These demos show:

1. **Basic usage**: Environment initialization and simple actions
2. **Action bundles**: Registering and using high-level actions
3. **Multi-agent**: Hierarchical agent coordination
4. **Views**: Restricted action spaces
5. **Real tasks**: Practical automation examples
6. **Observations**: Understanding the observation space
7. **Interactive**: Live demo with user input
8. **Agent integration**: How GDP agents use the environment

For more details:
- See `agentvm/desktop_env.py` for implementation
- See `gdpagent/run_mm_task.py` for full multi-agent system
- See `actions/` for available action bundles
