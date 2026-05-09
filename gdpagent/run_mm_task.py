import sys
from pathlib import Path

# Ensure repository root and package dirs are on sys.path before local imports
CURRENT_FILE = Path(__file__).resolve()
PROJECT_DIR = CURRENT_FILE.parent
REPO_ROOT = PROJECT_DIR.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))
if str(PROJECT_DIR) not in sys.path:
	sys.path.insert(0, str(PROJECT_DIR))

ACTIONS_DIR = REPO_ROOT / "actions"

import argparse
import json
import logging
import os
from typing import Any, Callable

import yaml

from agentic_machines.agents.base_agent import BaseAgent
from agentic_machines.memory.message_pruning import prune_tool_messages
from agentic_machines.config import get_llm_config, set_llm_caller_config

from gdpagent.utils.action_calls import build_action_dispatch
from gdpagent.utils.schema_utils import parse_yaml_schema
from gdpagent.agents.mm_agent import MMAgent
from gdpagent.agents.cua_agent import CUAAgent
from gdpagent.agents.grounding import OSWorldACI, GroundingActionExecutor
from dotenv import load_dotenv

from agentvm.desktop_env import DesktopEnv


logging.basicConfig(
	level=logging.ERROR,
	format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

DEFAULT_LLM_MODEL = "gpt-5.2"
DEFAULT_GROUNDING_WIDTH = 1366
DEFAULT_GROUNDING_HEIGHT = None

# Special actions that should not be converted to CUA wrapper functions
SPECIAL_ACTIONS = {"switch_window", "open_app", "close_window"}

CONTROL_AGENT_SCHEMA = {
	"type": "function",
	"function": {
		"name": "control_agent",
		"description": "Control agent to manage subtasks and call other agents as needed.",
		"parameters": {
			"type": "object",
			"properties": {"task": {"type": "string"}},
			"required": ["task"],
			"additionalProperties": False,
		},
		"strict": True,
	},
}


def _switch_window(env: DesktopEnv, window_id: str = None, name: str = None) -> str:
	# Support both 'window_id' and legacy 'name' parameter
	wid = window_id or name
	if not wid:
		return json.dumps({"status": "error", "message": "window_id is required"})
	
	action_input = {
		"action_type": "switch_window",
		"arguments": {"window_id": wid},
	}
	observation, *_ = env.step(action_input)
	out = observation.get("action_output", {})
	# Normalize into a readable string for the agent
	if isinstance(out, dict):
		return json.dumps(out, ensure_ascii=False)
	return str(out)


def _close_window(env: DesktopEnv, window_id: str = None) -> str:
	action_input = {
		"action_type": "close_window",
		"arguments": {},
	}
	if window_id is not None:
		action_input["arguments"]["window_id"] = window_id
	observation, *_ = env.step(action_input)
	out = observation.get("action_output", {})
	# Normalize into a readable string for the agent
	if isinstance(out, dict):
		return json.dumps(out, ensure_ascii=False)
	return str(out)


def _open_app(env: DesktopEnv, app_name: str, path: str = None) -> str:
	action_input = {
		"action_type": "open_app",
		"arguments": {"app_name": app_name},
	}
	if path is not None:
		action_input["arguments"]["path"] = path
	observation, *_ = env.step(action_input)
	out = observation.get("action_output", {})
	# Normalize into a readable string for the agent
	if isinstance(out, dict):
		return json.dumps(out, ensure_ascii=False)
	return str(out)


def _allocate_run_dir(base_dir: str) -> str:
	"""Allocate a unique per-run directory under base_dir named run_N.

	Example: base_dir=results/lawyer_task -> results/lawyer_task/run_0
	"""
	base = Path(base_dir)
	if base.exists() and not base.is_dir():
		raise ValueError(f"save_path must be a directory, got file: {base}")
	base.mkdir(parents=True, exist_ok=True)

	idx = 0
	while (base / f"run_{idx}").exists():
		idx += 1

	run_dir = base / f"run_{idx}"
	run_dir.mkdir(parents=True, exist_ok=False)
	return str(run_dir)

def _load_tool_schemas(tool_names: list[str]) -> list[dict]:
	schemas: list[dict] = []
	for name in tool_names:
		schemas += parse_yaml_schema(ACTIONS_DIR / name / "schema.yaml")
	return schemas


def _load_yaml(path: str) -> dict:
	with open(path, "r", encoding="utf-8") as f:
		data = yaml.safe_load(f)
	if not isinstance(data, dict):
		raise ValueError(f"YAML at {path} must be a mapping/object")
	return data


def _get_nested(d: dict, keys: list[str], default: Any = None) -> Any:
	cur: Any = d
	for k in keys:
		if not isinstance(cur, dict) or k not in cur:
			return default
		cur = cur[k]
	return cur


def _make_cua_wrapper(action_name: str, cua_agent: Any) -> Callable[..., Any]:
	"""Create a wrapper function that converts action parameters into a task string for CUA agent.
	
	Args:
		action_name: The name of the action (e.g., 'click', 'type')

		cua_agent: The CUA agent instance to call
		
	Returns:
		A callable that accepts the action parameters and calls cua_agent.run()
	"""
	def wrapper(**kwargs: Any) -> Any:
		# Build task description from parameters
		task_parts = [f"Please perform a {action_name} action with the following details:"]
		
		for param_name, param_value in kwargs.items():
			task_parts.append(f"  {param_name}: {param_value}")
		
		task_str = "\n".join(task_parts)
		return cua_agent.run(task_str)
	
	return wrapper


def _build_gui_action_dispatch(
	env: DesktopEnv, cua_agent: Any, schemas: list[dict]
) -> tuple[dict[str, Callable], list[dict]]:
	"""Build dispatch mapping and schemas for GUI actions.
	
	Args:
		env: The DesktopEnv to execute direct GUI actions in.
		cua_agent: The CUA agent instance
		schemas: List of GUI action schemas from JSON
		
	Returns:
		Tuple of (action_dict, schema_list) where action_dict maps action names to callables
		and schema_list contains the tool schemas for the LLM
	"""
	action_dict: dict[str, Callable] = {}
	tool_schemas: list[dict] = []
	# When running with --gui_executor=cua we load CUA schemas (e.g. `cua_action_schemas.json`)
	# which include tools like `double_click`, `drag`, and `move`. Route these to the CUA agent.
	# Keep `drag_and_drop` for compatibility with the non-CUA GUI schema/tool naming.
	cua_actions = {"click", "double_click", "scroll", "drag", "move", "drag_and_drop"}

	# TODO: type for cua agent: _type(). type for gui (agent s grounding): need to 

	def _run_pyautogui(command: str) -> str:
		observation, *_ = env.step(command)
		out = observation.get("action_output", None)
		if isinstance(out, dict):
			return json.dumps(out, ensure_ascii=False)
		return str(out) if out else "Action completed"

	def _hotkey(keys: list[str]) -> str:
		# Follow gdpagent/agents/grounding.py hotkey()
		keys_args = ", ".join(repr(k) for k in (keys or []))
		return _run_pyautogui(f"import pyautogui; pyautogui.hotkey({keys_args})")

	def _hold_and_press(hold_keys: list[str], press_keys: list[str]) -> str:
		# Follow gdpagent/agents/grounding.py hold_and_press()
		command = "import pyautogui; "
		for k in hold_keys or []:
			command += f"pyautogui.keyDown({repr(k)}); "
		press_keys_list = "[" + ", ".join(repr(k) for k in (press_keys or [])) + "]"
		command += f"pyautogui.press({press_keys_list}); "
		for k in hold_keys or []:
			command += f"pyautogui.keyUp({repr(k)}); "
		return _run_pyautogui(command)

	def _wait(time: float) -> str:
		# Follow gdpagent/agents/grounding.py wait()
		return _run_pyautogui(f"import time; time.sleep({float(time)})")
	
	def _type(text: str) -> str:
		return _run_pyautogui(f"import pyautogui; pyautogui.typewrite({repr(text)})")
	
	for schema in schemas:
		if schema.get("type") != "function":
			continue
			
		func_def = schema.get("function", {})
		action_name = func_def.get("name")
		
		if not action_name:
			continue
		
		# Skip special actions - they're handled in main()
		if action_name in SPECIAL_ACTIONS:
			continue
		
		# Only click/type/scroll/drag_and_drop should route to CUA.
		if action_name in cua_actions:
			action_dict[action_name] = _make_cua_wrapper(action_name, cua_agent)
		elif action_name == "hotkey":
			action_dict[action_name] = _hotkey
		elif action_name == "hold_and_press":
			action_dict[action_name] = _hold_and_press
		elif action_name == "wait":
			action_dict[action_name] = _wait
		elif action_name == "type":
			action_dict[action_name] = _type
		else:
			raise ValueError(
				f"Unknown GUI action '{action_name}' in loaded GUI schemas. "
				"Add an implementation here or route it to CUA explicitly."
			)
		tool_schemas.append(schema)
	
	return action_dict, tool_schemas


def _build_aci_action_dispatch(
	executor: GroundingActionExecutor,
	schemas: list[dict],
) -> tuple[dict[str, Callable], list[dict]]:
	"""Build dispatch mapping and schemas for GUI actions using grounding ACI."""
	action_dict: dict[str, Callable] = {}
	tool_schemas: list[dict] = []

	for schema in schemas:
		if schema.get("type") != "function":
			continue

		func_def = schema.get("function", {})
		action_name = func_def.get("name")

		if not action_name:
			continue

		# Skip special actions - they're handled in main()
		if action_name in SPECIAL_ACTIONS:
			continue

		action_dict[action_name] = lambda _name=action_name, **kwargs: executor.run_action(_name, **kwargs)
		tool_schemas.append(schema)

	return action_dict, tool_schemas


def main() -> None:
	load_dotenv(dotenv_path=PROJECT_DIR / ".env", override=True)

	parser = argparse.ArgumentParser()
	parser.add_argument(
		"--task",
		"-t",
		type=str,
		required=True,
		help="Path to task YAML bundle (task + optional task setup_config + agents)",
	)
	parser.add_argument("--save_path", type=str, default="./task_run")
	parser.add_argument("--cache_seed", type=int, default=None)
	parser.add_argument(
		"--gui_executor",
		"-g",
		type=str,
		choices=["cua", "aci"],
		default="cua",
		help="Choose GUI executor: 'cua' for CUA agent or 'aci' for grounding ACI.",
	)
	parser.add_argument("--aci_generation_provider", type=str, default=os.environ.get("MODEL_PROVIDER", "openai"))
	parser.add_argument("--aci_generation_model", type=str, default=os.environ.get("MODEL", DEFAULT_LLM_MODEL))
	parser.add_argument("--aci_generation_base_url", type=str, default=os.environ.get("MODEL_URL", ""))
	parser.add_argument("--aci_generation_api_key", type=str, default=os.environ.get("OPENAI_API_KEY", ""))
	parser.add_argument(
		"--aci_temperature",
		type=float,
		default=float(os.environ.get("MODEL_TEMPERATURE", "1.0")),
	)
	parser.add_argument("--aci_grounding_provider", type=str, default=os.environ.get("GROUND_PROVIDER", "parasail"))
	parser.add_argument("--aci_grounding_model", type=str, default=os.environ.get("GROUND_MODEL", ""))
	parser.add_argument("--aci_grounding_base_url", type=str, default=os.environ.get("GROUND_URL", ""))
	parser.add_argument("--aci_grounding_api_key", type=str, default=os.environ.get("GROUND_API_KEY", ""))
	parser.add_argument("--aci_grounding_width", type=int, default=os.environ.get("GROUND_WIDTH", DEFAULT_GROUNDING_WIDTH))
	parser.add_argument("--aci_grounding_height", type=int, default=os.environ.get("GROUND_HEIGHT", DEFAULT_GROUNDING_HEIGHT))
	parser.add_argument("--aci_sleep_after_execution", type=float, default=0.3)
	args = parser.parse_args()

	# Load task bundle YAML.
	# If task is just a name (no path separators), look in tasks/run_yaml/
	task_path = args.task
	if "/" not in task_path and "\\" not in task_path:
		# Just a task name, construct the path
		if not task_path.endswith(".yaml"):
			task_path = f"{task_path}.yaml"
		task_path = str(PROJECT_DIR / "tasks" / task_path)
	
	task_bundle = _load_yaml(task_path)
	agent_cfg = task_bundle.get("agents", {})
	task_config = task_bundle.get("task", {})

	# Treat save_path as a base folder. Namespace it by the task YAML filename,
	# then allocate run_0/run_1/... under it for each execution.
	task_yaml_stem = Path(task_path).stem
	base_save_path = str(Path(args.save_path) / task_yaml_stem)
	save_path = _allocate_run_dir(base_save_path)
	print(f"Save path: {save_path}")

	# copy yaml to save_path for record keeping
	import shutil
	shutil.copy(task_path, Path(save_path) / "task.yaml")

	# -------------------- Environment setup --------------------
	# Default environment init/setup live in code (so env_all_actions.yaml is not required).
	default_init_cfg: dict = {
		"provider_name": "docker",
		"path_to_vm": "/home/yiran/osworld/docker_vm_data/Ubuntu.actions.qcow2",
		"os_type": "Ubuntu",
		"headless": False,
	}
	init_cfg: dict = task_bundle.get("init", default_init_cfg) or default_init_cfg
	if not isinstance(init_cfg, dict):
		raise ValueError("task: init must be a mapping/object if provided")

	default_setup_config: list[dict] = [
		{
			"type": "register_action",
			"parameters": {
				"actions": [
					{"local_path": str(ACTIONS_DIR / "execute_bash")},
					{"local_path": str(ACTIONS_DIR / "file_reader")},
					{"local_path": str(ACTIONS_DIR / "pandoc_converter")},
					{"local_path": str(ACTIONS_DIR / "run_python")},
					{"local_path": str(ACTIONS_DIR / "str_replace_editor")},
					{"local_path": str(ACTIONS_DIR / "text_web_browser")},
				]
			}
		}
	]
	setup_config = default_setup_config + (task_bundle.get("setup_config", []) or [])

	# init_cfg fields follow the pattern in gdpagent/order_analyst.yaml
	env = DesktopEnv(**init_cfg)

	print("Starting OSWorld environment...")
	env.reset(setup_config=setup_config, task_config=task_config)
	print("Environment reset complete!")

	if hasattr(env, "vnc_port"):
		print(f"🌐 VNC Web Access: http://localhost:{env.vnc_port}")


	# =====================================================
	# -------------------- Agent setup --------------------
	# LLM configs
	set_llm_caller_config(cache_seed=args.cache_seed)
	controller_llm_config = _get_nested(agent_cfg, ["controller", "llm_config"], default={}) or {}

	# Treat llm_config as kwargs for get_llm_config; apply a default model/cache_seed.
	default_llm_config = get_llm_config(
		**{
			"model": controller_llm_config.get("model", DEFAULT_LLM_MODEL),
			"cache_seed": args.cache_seed,
			**{k: v for k, v in controller_llm_config.items() if k not in {"model", "cache_seed"}},
		}
	)

	# Build callables for every action folder under ./actions. Return dict of action_name -> callable.
	full_tool_dispatch = build_action_dispatch(env, actions_dir=ACTIONS_DIR)

	# Create CUA agent
	cua_cfg = agent_cfg.get("cua", {})

	# ---------------- Controller agent setup --------------------
	# 1. Load GUI action schemas and action callables to controller's action dict


	if args.gui_executor == "cua":
		with open(PROJECT_DIR / "cua_action_schemas.json", "r", encoding="utf-8") as f:
			gui_schemas = json.load(f)
		cua_agent = CUAAgent(
			max_step=int(cua_cfg.get("max_step", 20)),
			save_path=save_path,
			client_password=str(cua_cfg.get("client_password", "password")),
			env=env,
		)
		gui_action_dict, gui_tool_schemas = _build_gui_action_dispatch(env, cua_agent, gui_schemas)
	else:
		gui_schemas_path = PROJECT_DIR / "gui_action_schemas.json"
		with open(gui_schemas_path, "r", encoding="utf-8") as f:
			gui_schemas = json.load(f)
		engine_params_for_generation = {
			"engine_type": args.aci_generation_provider,
			"model": args.aci_generation_model,
			"base_url": args.aci_generation_base_url,
			"api_key": args.aci_generation_api_key,
			"temperature": args.aci_temperature,
		}
		engine_params_for_grounding = {
			"engine_type": args.aci_grounding_provider,
			"model": args.aci_grounding_model,
			"base_url": args.aci_grounding_base_url,
			"api_key": args.aci_grounding_api_key,
			"grounding_width": args.aci_grounding_width,
			"grounding_height": args.aci_grounding_height,
		}

		# DesktopEnv.controller.get_vm_screen_size() returns a JSON object (dict) in most
		# setups (e.g., {"width": 1920, "height": 1080}). Unpacking a dict yields its
		# keys ("width", "height"), which then breaks coordinate scaling.
		vm_screen_size = env.vm_screen_size
		if isinstance(vm_screen_size, dict):
			vm_width = vm_screen_size.get("width") or vm_screen_size.get("screen_width")
			vm_height = vm_screen_size.get("height") or vm_screen_size.get("screen_height")
			if vm_width is None or vm_height is None:
				raise ValueError(f"Unexpected vm_screen_size dict: {vm_screen_size}")
			vm_width = int(float(vm_width))
			vm_height = int(float(vm_height))
		elif isinstance(vm_screen_size, (list, tuple)) and len(vm_screen_size) == 2:
			vm_width = int(float(vm_screen_size[0]))
			vm_height = int(float(vm_screen_size[1]))
		else:
			raise ValueError(f"Unexpected vm_screen_size type/value: {type(vm_screen_size)} {vm_screen_size}")
		platform = "linux" if str(init_cfg.get("os_type", "linux")).lower().startswith("ubuntu") else "windows"
		aci_agent = OSWorldACI(
			platform=platform,
			engine_params_for_generation=engine_params_for_generation,
			engine_params_for_grounding=engine_params_for_grounding,
			width=vm_width,
			height=vm_height,
		)
		aci_executor = GroundingActionExecutor(
			aci_agent=aci_agent,
			env=env,
			sleep_after_execution=args.aci_sleep_after_execution,
		)
		gui_action_dict, gui_tool_schemas = _build_aci_action_dispatch(aci_executor, gui_schemas)

	# Initialize controller with GUI actions
	controller_action_dict: dict[str, Any] = gui_action_dict.copy()
	controller_action_schemas: list[dict] = gui_tool_schemas.copy()
	
	# 2. Add special actions and schemas directly to controller action dict
	special_action_funcs = {
		"switch_window": lambda **kwargs: _switch_window(env, **kwargs),
		"close_window": lambda **kwargs: _close_window(env, **kwargs),
		"open_app": lambda **kwargs: _open_app(env, **kwargs),
	}
	#  - Load schemas for special actions
	controller_action_dict.update(special_action_funcs)
	for schema in gui_schemas:
		if schema.get("type") == "function":
			action_name = schema.get("function", {}).get("name")
			if action_name in SPECIAL_ACTIONS:
				controller_action_schemas.append(schema)

	# 3. Build subagents and add to controller's action dict
	subagent_cfgs = agent_cfg.get("subagents", [])
	if not isinstance(subagent_cfgs, list):
		raise ValueError("agents: 'subagents' must be a list")

	for cfg in subagent_cfgs:
		# 3.1 create subagent
		agent_name = cfg.get("name") or cfg.get("call_name")
		if not agent_name:
			raise ValueError("Each subagent must include a non-empty 'name' (preferred) or 'call_name' (legacy)")
		
		agent_tools: list[str] = list(cfg.get("tools", []))
		missing_tools = [t for t in agent_tools if t not in full_tool_dispatch]
		if missing_tools:
			raise ValueError(
				f"Unknown tool(s) {missing_tools}. Ensure they exist under ./actions and are included in setup_config.register_action."
			)

		agent_llm_config = cfg.get("llm_config", default_llm_config.copy())
		if isinstance(agent_llm_config, dict):
			agent_llm_config["cache_seed"] = args.cache_seed
		
		subagent = BaseAgent(
			name=agent_name,
			description=cfg.get("description", f"Call subagent {agent_name}."),
			system_msg=cfg["system_msg"],
			max_step=int(cfg.get("max_step", 15)),
			reset_before_call=bool(cfg.get("reset_before_call", True)),
			action_schemas=_load_tool_schemas(agent_tools),
			allowed_action_dict={t: full_tool_dispatch[t] for t in agent_tools},
			llm_config=agent_llm_config,
			summarize_llm_config=cfg.get("summarize_llm_config", agent_llm_config),
			message_pruning_function=prune_tool_messages,
			save_path=save_path,
		)

		# 3.2 add to controller action dict
		controller_action_dict[agent_name] = subagent.run
		controller_action_schemas.append(subagent.schema())

	# 4. Add controller direct tools
	controller_tools = list(_get_nested(agent_cfg, ["controller", "tools"], default=[]) or [])
	if not isinstance(controller_tools, list):
		raise ValueError("agents: controller.tools must be a list")
	temp_schemas = _load_tool_schemas(controller_tools)
	for tool_name in controller_tools:
		if tool_name not in full_tool_dispatch:
			raise ValueError(
				f"Unknown controller tool '{tool_name}'. Ensure it exists under ./actions and is included in setup_config.register_action."
			)
		controller_action_dict[tool_name] = full_tool_dispatch[tool_name]

	controller_action_schemas += temp_schemas
	
	# --------------------------------------------------------------
	# ------------- Create and Run controller agent ----------------
	controller_system_msg = _get_nested(agent_cfg, ["controller", "system_msg"], default="")
	if not controller_system_msg:
		raise ValueError("agents: controller.system_msg must be provided and non-empty")

	controller_agent = MMAgent(
		system_msg=_get_nested(agent_cfg, ["controller", "system_msg"], default=""),
		name=CONTROL_AGENT_SCHEMA["function"]["name"],
		description=CONTROL_AGENT_SCHEMA["function"]["description"],
        max_images=5,
		max_step=int(_get_nested(agent_cfg, ["controller", "max_step"], default=25)),
		reset_before_call=True,
		allowed_action_dict=controller_action_dict,
		action_schemas=controller_action_schemas,
		gui_tool_schemas=gui_schemas,
		llm_config=default_llm_config,
		summarize_llm_config=default_llm_config,
		message_pruning_function=prune_tool_messages,
	)

	final_result = controller_agent.run(
		task="Below is your current role and assigned task:\n" + task_config["instruction"],
		env=env,
		save_folder=save_path,
	)
	print("\nFinal result:")
	try:
		print(final_result.output)
	except Exception:
		print(str(final_result))

	# Optional: sync VM folders back to host for inspection
	try:
		env.controller.get_folder(folder_path="/home/user/Downloads/", dest_path=f"{save_path}/Downloads")
	except Exception as e:
		print(f"Warning: could not sync VM folders: {e}")


if __name__ == "__main__":
	main()
