

# Case Study: Running Agent with AgentVM to solve real-world digital tasks


## Installation and Setup

Make sure your environment is set up and activated. Then, install this package:
```bash
git clone https://github.com/yiranwu0/agentic-machines
cd agentic-machines
uv pip install -e .
cd ..
```

Copy `example_env` to `.env` and fill in the necessary API keys. In the example, we use OpenAI models.

```bash
cp gdpagent/example_env gdpagent/.env
```

## Running the Agent
You can run the agent with the following command:

```bash
python gdpagent/run_mm_task.py -t medical_task
```

Checkout the run in `./task_run/medical_task/` for detailed logs, screenshots, and observations.

`-t/--task` accepts either:
- a task name (no slashes), which resolves to `gdpagent/tasks/<name>.yaml`, or
- an explicit path to a YAML file.

Examples:

```bash
# Run by task name (looks up gdpagent/tasks/medical_task.yaml)
python gdpagent/run_mm_task.py -t medical_task --save_path ./task_run
```

Key CLI parameters (see `python gdpagent/run_mm_task.py -h` for the full list):

- `-t/--task` (required): task YAML bundle name or path.
- `--save_path` (default: `./task_run`): base output folder. The runner will create
    `save_path/<task_yaml_stem>/run_0`, `run_1`, ... automatically.
- `--cache_seed` (optional): sets the LLM caller cache seed (useful for repeatability).
- `-g/--gui_executor` (default: `cua`): choose `cua` (CUA-backed) or `aci` (grounding executor).

ACI-specific parameters (only used when `-g aci`):

- For the full ACI-related CLI flags and defaults, run `python gdpagent/run_mm_task.py -h` or read `gdpagent/run_mm_task.py`.

## File Structure

Keep it simple—these are the two folders you’ll touch most often:

```text
gdpagent/
    agents/
        mm_agent.py      # main multimodal agent loop (orchestrates actions + observations)
        cua_agent.py     # CUA-backed GUI action implementation
        grounding.py     # helpers for grounding GUI actions
    tasks/
    medical_task.yaml  # task bundle (used by `-t medical_task`)
    admin_task.yaml    # task bundle (used by `-t admin_task`)
        other_tasks/       # extra task definitions / variants
```

Each `tasks/*.yaml` is a “task bundle” that configures the VM + the agent(s). At a high level:

- `init`: how to start the VM (provider, image path, OS type, headless, etc.).
- `setup_config`: pre-task setup steps inside the VM (e.g., install packages, upload files).
- `task`: the actual task definition:
    - `id`: task identifier
    - `instruction`: the prompt/instructions given to the agent
    - `evaluator`: how success is checked (optional; varies by task)
- `agents`: agent configuration used by the runner:
    - `cua`: settings for the CUA agent (when `-g cua`)
    - `controller`: controller tools + LLM config + system prompt
    - `subagents`: optional list of tool-specialized subagents (if provided by the task)


## The Agent

Below we explain how we build the agent to solve gdpval tasks. We have one main agent that can execute the following actions:

Text-based actions:
- `run_python`: run python code and return the output as text.
- `execute_bash`: execute bash commands and return the output as text.
- other text-based actions specified in task's yaml (e.g., `file_reader`)

Choose one of the two GUI action groups:
- "cua", backed by OpenAI CUA model.
    - atomic: `click`, `double_click`, `drag`, `scroll`, `move`, `hotkey`, `hold_and_press`, `wait`, `type`
    - advanced: `open_app`, `switch_window`, `close_window`
- "aci", backed by UI-TARS-1.5-7B.
    - atomic: `click`, `drag`, `scroll`, `hotkey`, `hold_and_press`, `wait`, `type`
    - advanced: `open_app`, `switch_window`, `close_window`

The agent are given these actions and a task description. 
When the agent calls the text-based actions:
    - the observation will be the text output returned by the action. 
    
When the agent calls the GUI actions:
    - The function call will be parsed, and then sent to the OpenAI CUA model or UI-TARS-1.5-7B to ground the action in pixels (Following Agent-S 2.5)
    - The observation will be the screenshot and also text-based state description (what windows are open, what is the title of the current window, etc.).