# AgentVM 
[Website](https://ag2ai.github.io/agentvm-page/) [Paper](https://openreview.net/pdf?id=XZaRHkbc2u)

AgentVM provides a desktop automation environment for LLM agents to interact with virtual machines through GUI and text-based actions. It is designed to be flexible to mount any skills and tools to be used in one unified Virtual Machine.

## 🎉 News
- **[2026/05/09]**: The Code and Paper Uploaded.
- **[2026/04/31]**: Our paper "Position: Digital Agents Require Unified Agent-Native Computers" is accepted by ICML Position Track 2026!
- **[2026/04/21]**: The AgentVM repo is created. Code and Paper Coming soon!


## 🚀 Installation and Quick Start

We recommend using `uv` to manage the python environment. 

```bash
# prepare uv environment
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uv venv --python 3.12
source .venv/bin/activate
```

```bash
uv pip install -e . # install in development mode
```

Run the command below to setup the VM (AgentVM is built on top of [OSWorld](https://github.com/xlang-ai/OSWorld), you can refer to it for VM image preparation and setup):

```bash
mkdir -p docker_vm_data
wget -c -O docker_vm_data/Ubuntu.qcow2.zip \
  "https://huggingface.co/datasets/xlangai/ubuntu_osworld/resolve/main/Ubuntu.qcow2.zip"
unzip -o docker_vm_data/Ubuntu.qcow2.zip -d docker_vm_data
sudo bash bake_actions.sh 
```

Run the quickstart to start and open the VM:
```bash
python live_quickstart.py
```
Note: at first run, the VM image will be downloaded.
A local url is provided to open the VM in your browser and you can interact with the VM through the webpage.

Remember to check what you are running in docker using `docker ps` and stop unused containers.

## 🧪 Example Usage

AgentVM can be configured to solve any type of digital tasks. To demonstrate this, we test with real-world
[GDPval tasks](https://openai.com/index/gdpval/). 
Setup the agent in [gpdagent folder](gdpagent/README.md) to see what the agent can do!


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

## 🧰 Python API Quickstart

Use `DesktopEnv` directly when you want to script the VM interaction loop. The snippets below are meant to be run in order as one script.

<details>
<summary>1. Create the VM-backed environment</summary>

Create a `DesktopEnv` with the Docker provider and point it to your prepared VM image.

```python
from agentvm.desktop_env import DesktopEnv

env = DesktopEnv(
    provider_name="docker",
    path_to_vm="/path/to/Ubuntu.qcow2",
    os_type="Ubuntu",
    headless=False
)
```

</details>

<details>
<summary>2. Register actions and reset the VM</summary>

Pass setup instructions to `reset()` before the first observation. This example registers `execute_bash` as a text-based action.

```python
setup_config = [
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": "./actions/execute_bash"}
            ]
        }
    }
]
obs = env.reset(setup_config=setup_config)
```

See [actions/README.md](actions/README.md) for details on creating and registering new actions.

</details>

<details>
<summary>3. Send GUI and text-based actions</summary>

Use atomic GUI actions for pixel-level control, advanced GUI actions for common desktop operations, and text-based actions when the agent needs structured command output.

```python
# Atomic GUI actions
env.step({"action_type": "CLICK", "arguments": {"x": 100, "y": 200}})
env.step({"action_type": "TYPING", "arguments": {"text": "Hello"}})
env.step({"action_type": "HOTKEY", "arguments": {"keys": ["ctrl", "c"]}})

# Advanced GUI action
env.step({
    "action_type": "open_window",
    "arguments": {"app_name": "firefox"}
})

# Text-based action
action = {
    "action_type": "execute_bash",
    "arguments": {"script": "ls -la /home/user"}
}
obs, reward, done, info = env.step(action)
```

</details>

<details>
<summary>4. Read observations and close the environment</summary>

Each step returns an observation that can combine screenshots, accessibility data, VM state, available actions, and output from text-based actions.

```python
print(obs['action_output']['output'])
# Output: drwxr-xr-x 2 user user 4096 ...

print(obs['state'])

env.close()
```

Observation structure:

```python
obs = {
    "screenshot": np.ndarray,           # RGB image (H, W, 3)
    "accessibility_tree": dict,         # UI element tree
    "state": dict,                      # VM state info, including what windows are open, what applications are running, etc
    "action_space": List[str],          # Available actions
    "action_output": dict,              # Output from the executed action (if applicable)
}
```

</details>

## 🛠️ Customization

AgentVM is built for developers to hack and customize. 

### ➕ 1. Add new actions

Follow this [README](actions/README.md) to create and add new actions to the environment.
Each action is an independent module with its own dependencies installed in a separate python venv. Actions can be added as MCPs.


### 🧁 2. Bake your environment for faster startup

You can pre-install actions / run setups of your VM through `bake_actions.sh`. You can modify it to install any actions, python packages, and other dependencies you need, and bake the image. The new image is names as `Ubuntu.actions.qcow2` by default. Switch by changing the `path_to_vm` parameter when initializing the environment.


## 📚 Citation

```
@inproceedings{wu2026agentnativecomputers,
  title={Position: Digital Agents Require Unified Agent-Native Computers},
  author={Wu, Yiran and Liu, Jiale and Zhang, Jieyu and Zhang, Yaolun and Liu, Shilong and Wang, Chi and Wang, Mengdi and Wang, Huazheng and Wu, Qingyun},
  booktitle={International Conference on Machine Learning},
  year={2026}
}
```

## 🙏 Acknowledgement

AgentVM is built on top of OSWorld, and also incorporate tools from [OSWorld-MCP](https://github.com/X-PLUG/OSWorld-MCP/tree/main). Check out these awesome projects!


### License

This repository is based in part on [OSWorld](https://github.com/xlang-ai/OSWorld),
which is licensed under the Apache License 2.0.

This repository is distributed under the Apache License 2.0. See the `LICENSE`
file for the full license text and the `NOTICE` file for attribution details in `agentvm/license_original`.

Where files are derived from OSWorld, the original notices are retained and
modifications are marked accordingly.
