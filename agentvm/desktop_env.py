from __future__ import annotations

import logging
import os
import time
import re
import json
from pathlib import Path
from typing import Callable, Any, Optional, Tuple
from typing import List, Dict, Union

import gymnasium as gym
import yaml

from agentvm.controllers.python import PythonController
from agentvm.controllers.setup import SetupController
from agentvm.evaluators import metrics, getters
from agentvm.providers import create_vm_manager_and_provider
from agentvm.action_util.action_bundle import ActionBundle
from agentvm.views import ActionScope, EnvView, AppView

logger = logging.getLogger("desktopenv.env")

Metric = Callable[[Any, Any], float]
Getter = Callable[[gym.Env, Dict[str, Any]], Any]

MAX_RETRIES = 5 # Maximum retries for environment setup
            


def _fix_pyautogui_less_than_bug(command: str) -> str:
    """
    Fix PyAutoGUI '<' character bug by converting it to hotkey("shift", ',') calls.
    
    This fixes the known PyAutoGUI issue where typing '<' produces '>' instead.
    References:
    - https://github.com/asweigart/pyautogui/issues/198
    - https://github.com/xlang-ai/OSWorld/issues/257
    
    Args:
        command (str): The original pyautogui command
        
    Returns:
        str: The fixed command with '<' characters handled properly
    """
    # Pattern to match press('<') or press('\u003c') calls  
    press_pattern = r'pyautogui\.press\(["\'](?:<|\\u003c)["\']\)'

    # Handle press('<') calls
    def replace_press_less_than(match):
        return 'pyautogui.hotkey("shift", ",")'
    
    # First handle press('<') calls
    command = re.sub(press_pattern, replace_press_less_than, command)

    # Pattern to match typewrite calls with quoted strings
    typewrite_pattern = r'pyautogui\.typewrite\((["\'])(.*?)\1\)'
    
    # Then handle typewrite calls
    def process_typewrite_match(match):
        quote_char = match.group(1)
        content = match.group(2)
        
        # Preprocess: Try to decode Unicode escapes like \u003c to actual '<'
        # This handles cases where '<' is represented as escaped Unicode
        try:
            # Attempt to decode unicode escapes
            decoded_content = content.encode('utf-8').decode('unicode_escape')
            content = decoded_content
        except UnicodeDecodeError:
            # If decoding fails, proceed with original content to avoid breaking existing logic
            pass  # English comment: Graceful degradation - fall back to original content if decoding fails
        
        # Check if content contains '<'
        if '<' not in content:
            return match.group(0)
        
        # Split by '<' and rebuild
        parts = content.split('<')
        result_parts = []
        
        for i, part in enumerate(parts):
            if i == 0:
                # First part
                if part:
                    result_parts.append(f"pyautogui.typewrite({quote_char}{part}{quote_char})")
            else:
                # Add hotkey for '<' and then typewrite for the rest
                result_parts.append('pyautogui.hotkey("shift", ",")')
                if part:
                    result_parts.append(f"pyautogui.typewrite({quote_char}{part}{quote_char})")
        
        return '; '.join(result_parts)
    
    command = re.sub(typewrite_pattern, process_typewrite_match, command)
    
    return command


class DesktopEnv(gym.Env):
    """
    DesktopEnv with OpenAI Gym interface. It provides a desktop environment for setting and evaluating desktop automation tasks.
    """
    
    def __init__(
            self,
            # start Virtual Machine related args
            provider_name: str = "vmware",
            region: str = None,
            path_to_vm: str = None,
            snapshot_name: str = "init_state",

            # VM related args
            cache_dir: str = "cache",
            screen_size: Tuple[int] = (int(os.environ.get("SCREEN_WIDTH", 1920)), int(os.environ.get("SCREEN_HEIGHT", 1080))),
            headless: bool = False,
            os_type: str = "Ubuntu",
            enable_proxy: bool = False,
            client_password: str = "",

            # about observation space
            require_a11y_tree: bool = False,
            require_terminal: bool = False,
            enable_mcp: bool = False,
    ):
        """
        Args:
            provider_name (str): virtualization provider name, default to "vmware"
            region (str): the region for allocate machines, work for cloud services, default to  "us-east-1"
            path_to_vm (str): path to .vmx file
            snapshot_name (str): snapshot name to revert to, default to "init_state"
            cache_dir (str): cache directory to cache task-related stuffs like
              reference file for evaluation
            screen_size (Tuple[int]): screen size of the VM
            headless (bool): whether to run the VM in headless mode
            require_a11y_tree (bool): whether to require accessibility tree
            require_terminal (bool): whether to require terminal output
            os_type (str): operating system type, default to "Ubuntu"
            enable_proxy (bool): whether to enable proxy support, default to False
        """
        # Initialize VM manager and vitualization provider
        self.region = region
        self.provider_name = provider_name
        self.enable_proxy = enable_proxy  # Store proxy enablement setting
        if client_password == "":
            if self.provider_name == "aws":
                self.client_password = "osworld-public-evaluation"
            else:
                self.client_password = "password"
        else:
            self.client_password = client_password
        self.screen_width = screen_size[0]
        self.screen_height = screen_size[1]

        # Default ports
        self.server_port = 5000
        self.chromium_port = 9222
        self.vnc_port = 8006
        self.vlc_port = 8080
        
        # Initialize with default (no proxy) provider
        self.current_use_proxy = False
        self.manager, self.provider = create_vm_manager_and_provider(provider_name, region, use_proxy=False)

        self.os_type = os_type

        # Track whether environment has been used (step/setup) to optimize snapshot revert
        # docker, aws, gcp, azure are always unused as the emulator starts from a clean state
        # vmware, virtualbox are always used as the emulator starts from a dirty state
        if self.provider_name in {"docker", "aws", "gcp", "azure", "aliyun", "volcengine", "apptainer"}:
            self.is_environment_used = False
        elif self.provider_name in {"vmware", "virtualbox"}:
            self.is_environment_used = True
        else:
            raise ValueError(f"Invalid provider name: {self.provider_name}")

        # Initialize environment variables
        if path_to_vm:
            self.path_to_vm = os.path.abspath(os.path.expandvars(os.path.expanduser(path_to_vm))) \
                if provider_name in {"vmware", "virtualbox"} else path_to_vm
        else:
            self.path_to_vm = self.manager.get_vm_path(os_type=self.os_type, region=region, screen_size=(self.screen_width, self.screen_height))
        
        self.snapshot_name = snapshot_name
        self.cache_dir_base: str = cache_dir
        # todo: add the logic to get the screen size from the VM
        self.headless = headless
        self.require_a11y_tree = require_a11y_tree
        self.require_terminal = require_terminal

        # Initialize emulator and controller
        logger.info("Initializing...")
        self._start_emulator()

        # mode: human or machine
        self.instruction = None

        # episodic stuffs, like counters, will be updated or reset
        # when calling self.reset()
        self._traj_no: int = -1
        self._step_no: int = 0
        self.action_history: List[Dict[str, any]] = []

        self.action_bundles: List[ActionBundle] = [] # loaded action bundles
        self.registered_actions: List[Dict[str, Any]] = [] # passed in config
        self.views: List[EnvView] = []
        self.enable_mcp = enable_mcp

    def _register_actions(self, actions_to_register: List[Dict[str, Any]]):
        """Setup action bundles from registered_actions configuration.
        """

        # Create Action Bundle for visibility
        self.action_bundles.clear()
        for action in actions_to_register:
            bundle = ActionBundle(
                local_path=action['local_path'],
                remote_path=action.get('remote_path') # TODO: remove this
            )
            self.action_bundles.append(bundle)
            logger.info(f"Loaded action bundle '{bundle.name}' with {len(bundle.commands)} commands")

        # register the available actions to python controller, so that each time a new env gets created, we can bind available actions to the controller
        self.controller._register_bundles(self.action_bundles)

    def execute_app_action(self, action: dict, timeout: int =120) -> Optional[Dict[str, Any]]:
        """Execute a registered action by finding its bundle and running it in the remote environment.
        
        Args:
            action: Dict containing "name" and optional "arguments" keys from LLM
            timeout: Execution timeout in seconds (default: 60)
            
        Returns:
            Dictionary with status, output, error, and returncode from the execution
            
        Raises:
            ValueError: If action is not found in any bundle
        """
        action_name = action.get("name")
        if not action_name:
            raise ValueError("Action dict must contain 'name' field")
        
        logger.info(f"Executing registered action: {action_name}")
        
        # Find which bundle has this action
        target_bundle = None
        for bundle in self.action_bundles:
            if bundle.has_action(action):
                target_bundle = bundle
                break
        
        if target_bundle is None:
            raise ValueError(f"Action '{action_name}' not found in any registered bundle")
        
        logger.debug(f"Action '{action_name}' found in bundle '{target_bundle.name}'")
        
        # Get the execution script from the bundle (bundle uses its own commands)
        bash_script = target_bundle.get_execution_script(action)
        
        logger.debug(f"Bash script to execute:\n{bash_script}")
        
        # Execute the bash script in the remote environment
        result = self.controller.run_bash_script(
            script=bash_script,
            timeout=timeout,
            working_dir=target_bundle.bundle_dir
        )
        
        if result and result.get("returncode") == 0:
            logger.info(f"Action '{action_name}' executed successfully")
        else:
            logger.error(f"Action '{action_name}' failed with return code: {result.get('returncode', -1)}")
            logger.error(f"Error output: {result.get('error', 'N/A')}")
        
        return result

    def _start_emulator(self):
        try:
            # Power on the virtual machine
            self.provider.start_emulator(self.path_to_vm, self.headless, self.os_type)

            # Get the ip from the virtual machine, and setup the controller
            vm_ip_ports = self.provider.get_ip_address(self.path_to_vm).split(':')
            self.vm_ip = vm_ip_ports[0]
            # Get the ports from the virtual machine (for Docker provider only)
            if len(vm_ip_ports) > 1:
                self.server_port = int(vm_ip_ports[1])
                self.chromium_port = int(vm_ip_ports[2])
                self.vnc_port = int(vm_ip_ports[3])
                self.vlc_port = int(vm_ip_ports[4])
            self.controller = PythonController(vm_ip=self.vm_ip, server_port=self.server_port)
            self.setup_controller = SetupController(vm_ip=self.vm_ip, server_port=self.server_port, chromium_port=self.chromium_port, vlc_port=self.vlc_port, cache_dir=self.cache_dir_base, client_password=self.client_password, screen_width=self.screen_width, screen_height=self.screen_height)

        except Exception as e:
            try:
                self.provider.stop_emulator(self.path_to_vm)
            except Exception as stop_err:
                logger.warning(f"Cleanup after interrupt failed: {stop_err}")
            raise

    def _revert_to_snapshot(self):
        # Revert to certain snapshot of the virtual machine, and refresh the path to vm and ip of vm
        # due to the fact it could be changed when implemented by cloud services
        path_to_vm = self.provider.revert_to_snapshot(self.path_to_vm, self.snapshot_name)
        if path_to_vm and not path_to_vm == self.path_to_vm:
            # path_to_vm has to be a new path 
            
            self.manager.delete_vm(self.path_to_vm, self.region)
            self.manager.add_vm(path_to_vm, self.region)
            self.manager.occupy_vm(path_to_vm, os.getpid(), self.region)
            self.path_to_vm = path_to_vm

    def _save_state(self, snapshot_name=None):
        # Save the current virtual machine state to a certain snapshot name
        self.provider.save_state(self.path_to_vm, snapshot_name)

    def close(self):
        # Close (release) the virtual machine
        self.provider.stop_emulator(self.path_to_vm)

    def reset(
        self,
        *,
        setup_config: Optional[List[Dict[str, Any]]] = None,
        task_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Reset the environment.

        This is the new reset entrypoint:
        - Preferred: pass `setup_config` (YAML's `setup_config`) and optionally `task` (YAML's `task`).
        - Legacy: pass `task_config` (the older OSWorld task dict). In that case, we delegate to
          `reset_with_osworld_tasks` unchanged.
        """
        if setup_config is None:
            # If user previously called from_yaml/from_json, allow using stored setup_config.
            setup_config = getattr(self, "setup_config", None)
        
        if task_config is None:
            task_config = getattr(self, "task_config", None)
        
        setup_config = setup_config or []
        if not isinstance(setup_config, list):
            raise TypeError("setup_config must be a list of setup operations")
        
        logger.info(f"Resetting environment: has_setup_config={bool(setup_config)} has_task_config={bool(task_config)} ...")
        self._traj_no += 1
        self._step_no = 0
        self.action_history.clear()
        self.current_use_proxy = self.enable_proxy
            
        done = False
        for attempt in range(MAX_RETRIES):
            # Only revert to snapshot if environment has been used (step/setup)
            # This optimization is especially important for cloud providers like AWS
            # where unnecessary snapshot operations are costly and time-consuming
            
            if self.is_environment_used:
                logger.info("Environment has been used, reverting to snapshot {}...".format(self.snapshot_name))
                self._revert_to_snapshot()
                logger.info("Starting emulator...")
                self._start_emulator()
                logger.info("Emulator started.")
                # Reset the usage flag after reverting
                self.is_environment_used = False
            else:
                logger.info("Environment is clean, skipping snapshot revert (provider: {}).".format(self.provider_name))
            
            # Handle task_config if provided (for task-specific setup)
            if task_config is not None:
                # if task_config, if proxy is not set or false, it should be false
                self.current_use_proxy = task_config.get("proxy", False) and self.enable_proxy
                if not self.enable_proxy and task_config.get("proxy", False):
                    logger.info("Task requires proxy but proxy is disabled at system level, ignoring proxy requirement.")
                
                self.task_id: str = task_config["id"]
                self.instruction = task_config["instruction"]
                if "config" in task_config:
                    logger.info("'config' field in task_config is deprecated, please use 'setup_config' instead.")
                    setup_config.extend(task_config["config"])
                if "setup_config" in task_config:
                    setup_config.extend(task_config["setup_config"])

                # Setup cache directory for the task
                self.cache_dir: str = os.path.join(self.cache_dir_base, self.task_id)
                os.makedirs(self.cache_dir, exist_ok=True)
                self.setup_controller.reset_cache_dir(self.cache_dir)

                # Set evaluator info if provided
                self._set_evaluator_info(task_config.get("evaluator", None))

            # Setup proxy if needed
            if self.current_use_proxy:
                self.setup_controller._proxy_setup(self.client_password)
            
            # Execute setup operations from setup_config
            if setup_config:
                logger.info("Setting up environment...")
                success = self.setup_controller.setup(setup_config, self.current_use_proxy)
                
                # Extract and register actions from setup_config
                actions_to_register = []
                for op in setup_config:
                    if op.get("type") == "register_action":
                        actions = op['parameters'].get("actions", []) or []
                        actions_to_register.extend(actions)
                if actions_to_register:
                    self._register_actions(actions_to_register)
                
                if success:
                    # Mark environment as used when setup is successfully executed
                    self.is_environment_used = True
                    done = True
                else:
                    logger.error(
                        "Environment setup failed, retrying (%d/%d)...",
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    time.sleep(5)
            else:
                done = True
            
            if done:
                break

        if self.enable_mcp: # TODO: Enable MCP in the VM
            self.setup_controller._launch_setup('soffice --accept="socket,host=localhost,port=2002;urp;" --norestore --nologo --nodefault', shell=True)
            self.setup_controller._launch_setup('cd ~/mcp_server/ && bash launch_server.sh', shell=True)
            time.sleep(5)
        
        logger.info("Environment setup complete.")
        
        # Clear existing views
        if self.views:
            for view in self.views:
                view.close()
        self.views.clear()
        
        observation = self._get_obs()
        return observation

    def get_mcp_tool_list(self, tool_name):
        ENV_SETTING = "import os; os.environ['PATH'] = '/home/user/.nvm/versions/node/v22.18.0/bin:/home/user/.local/bin:' + os.environ['PATH']; "

        command = ENV_SETTING
        command += f"from osworld_mcp_client import *; "
        command += f"OsworldMcpClient.list_tools(tool_name='{tool_name}', shuffle=False, rag=True); "
        tool_list = self.controller.execute_python_command(command)['output'].strip()

        try:
            tool_list = eval(tool_list)
        except Exception as e:
            print(e)
            tool_list = []

        return tool_list

    def call_mcp_tool(self, name, params):
        ENV_SETTING = "import os; os.environ['PATH'] = '/home/user/.nvm/versions/node/v22.18.0/bin:/home/user/.local/bin:' + os.environ['PATH']; "

        command = ENV_SETTING
        command += f"from osworld_mcp_client import *; "
        command += f"OsworldMcpClient.call_tool(name='{name}', params={str(params)}); "
        response = self.controller.execute_python_command(command)['output'].strip()

        return response


    def _get_obs(self, take_screenshot: bool = True) -> Dict[str, Any]:
        # We provide screenshot, accessibility_tree (optional), terminal (optional), and instruction.
        # can be customized and scaled
        return {
            "screenshot": self.controller.get_screenshot() if take_screenshot else None,
            "accessibility_tree": self.controller.get_accessibility_tree() if self.require_a11y_tree else None,
            "terminal": self.controller.get_terminal_output() if self.require_terminal else None,
            "instruction": self.instruction,
            "state": self.controller.get_state(),
            "action_space": self.get_action_space(),
            "window_state": self.get_window_state()
        }

    def get_window_state(self) -> str:
        """Get information about all active windows and the current active window.
        
        Returns:
            A string describing the open windows and which one is currently active.
        """
        try:
            # Use the controller's get_windows_status method
            status = self.controller.get_windows_status()
            
            if not status or not status.get("windows"):
                return "No active windows found"
            
            windows = status["windows"]
            active_window_id = status.get("active_window_id")
            
            # Format output
            output_lines = []
            
            # Show active window info
            if active_window_id and active_window_id in windows:
                active_win = windows[active_window_id]
                output_lines.append(f"Active window: {active_win.get('title', 'Unknown')} ({active_win.get('wm_class', 'Unknown')}) [ID: {active_window_id}]")
            else:
                output_lines.append("Active window: Unknown")
        except Exception as e:
            logger.error(f"Error retrieving window state: {e}")
            return "Error retrieving window state: " + str(e)
        
        output_lines.append(f"Total open windows: {len(windows)}")
        output_lines.append("")
        output_lines.append("Window list:")
        
        # List all windows
        for win_id, win_info in windows.items():
            is_active = (win_id == active_window_id)
            prefix = "  [ACTIVE] " if is_active else "  "
            wm_class = win_info.get("wm_class", "Unknown")
            title = win_info.get("title", "")
            output_lines.append(f"{prefix}[ID: {win_id}] {wm_class}: {title}")
        
        return "\n".join(output_lines)
            

    @property
    def vm_screen_size(self):
        return self.controller.get_vm_screen_size()


    def step(self, action, pause=2):
        # 1. action can be a string or a dict
        # if a string -> either pyautogui or 'WAIT', 'FAIL', 'DONE'
        #.   if in 'WAIT', 'FAIL', 'DONE' -> handle special actions and return
        #    if not, assume "pyautogui" in the string -> covert to dict {"name": "pyautogui", "command": str}
        # if a dict ->
        #.    - 1. pyautogui {"name": "pyautogui", "command": str}
        #     - 2. computer_13 action {"name": one of the computer_13 actions, "parameters": {...}}
        #     - 3. registered action {"name": str, "arguments": {...}}
        # Assume action is always a dict following openai function calling format {"name": str}

        self._step_no += 1
        self.action_history.append(action)
        
        # Mark environment as used when step is called
        self.is_environment_used = True

        reward = 0  # todo: Define reward calculation for each example
        done = False  # todo: Define episode termination condition for each example
        info = {}
        logger.info(f"Step {self._step_no} in trajectory {self._traj_no} with action: {action}")

        # Unified routing: no explicit action_space branching
        action_output = None
        SPECIAL = {'WAIT', 'FAIL', 'DONE'}
        COMPUTER13_ACTIONS = {
            'MOVE_TO', 'CLICK', 'MOUSE_DOWN', 'MOUSE_UP', 'RIGHT_CLICK', 'DOUBLE_CLICK',
            'DRAG_TO', 'SCROLL', 'TYPING', 'PRESS', 'KEY_DOWN', 'KEY_UP', 'HOTKEY'
        }

        # Temporary normalization for "name"/"arguments" format into action_type-based dict
        if isinstance(action, dict) and 'action_type' not in action and 'name' in action:
            action = {
                'action_type': action['name'],
                'arguments': action.get('arguments', action.get('parameters', {}))
            }

        take_screenshot = True
        # 1) Handle special actions early
        if (isinstance(action, str) and action in SPECIAL) or \
           (isinstance(action, dict) and action.get('action_type') in SPECIAL):
            if (action == 'WAIT') or (isinstance(action, dict) and action.get('action_type') == 'WAIT'):
                time.sleep(pause)
            elif (action == 'FAIL') or (isinstance(action, dict) and action.get('action_type') == 'FAIL'):
                done = True
                info = {"fail": True}
            elif (action == 'DONE') or (isinstance(action, dict) and action.get('action_type') == 'DONE'):
                done = True
                info = {"done": True}
        elif isinstance(action, str) or \
            (isinstance(action, dict) and action.get('action_type') == 'pyautogui') or \
            (isinstance(action, dict) and 'command' in action and 'action_type' not in action):
                # This should be pyautogui command
                fixed_command = _fix_pyautogui_less_than_bug(action)
                self.controller.execute_python_command(fixed_command)
        else:
            assert isinstance(action, dict), "Bug: Action should be a dict at this point"
            assert 'action_type' in action, "Bug: Action dict must contain 'action_type' field here."
                
            act_type = action.get('action_type')
            if act_type in COMPUTER13_ACTIONS:
                self.controller.execute_action(action)
            else:
                action_output = self.controller.execute_registered_action(action)

        time.sleep(pause)
        observation = self._get_obs(take_screenshot=take_screenshot)
        if action_output is not None:
            observation['action_output'] = action_output
        return observation, reward, done, info
    
    def create_view(
        self,
        *,
        actions: Optional[List[str]] = None,
        bundles: Optional[List[str]] = None,
        name: Optional[str] = None,
    ) -> "EnvView":
        """
        Create a restrictive view of this environment where only a subset of actions is allowed.
        - actions: explicit list of allowed action identifiers (action_type/name)
        - bundles: list of ActionBundle names to expand into allowed actions
        """
        scope = ActionScope(
            actions=set(actions) if actions else None,
            bundles=set(bundles) if bundles else None,
        )
        view = EnvView(
            env=self,
            scope=scope,
            name=name,
        )
        self.views.append(view)
        return view

    def create_app_view(
        self,
        app_name: str,
        name: Optional[str] = None,
    ) -> "AppView":
        """
        Open an application by name and create an AppView bound to its window.

        The resulting view:
        - Restricts actions to those coming from the bundle whose name matches `app_name`.
        - Replaces the full-screen screenshot with a window-only screenshot for that app.
        """
        # Launch the app and get its window id via the controller.
        result = self.controller.open_app(app_name=app_name)
        if not result or result.get("status") != "success":
            error_msg = (result or {}).get("error") or (result or {}).get("message") or "unknown error"
            raise RuntimeError(f"Failed to open app '{app_name}': {error_msg}")

        window_id = result.get("window_id")
        if not window_id:
            raise RuntimeError(f"Failed to obtain window id for app '{app_name}'")

        scope = ActionScope(
            actions=None,
            bundles={app_name},
        )
        view = AppView(
            env=self,
            scope=scope,
            name=name or app_name,
            app_name=app_name,
            window_id=window_id,
        )
        self.views.append(view)
        return view

    
    def get_action_space(self) -> List[str]:
        return self.controller.get_action_space()


    def reset_with_osworld_tasks(self, task_config: Optional[Dict[str, Any]] = None, seed=None, options=None) -> Dict[str, Any]:
        
        # Reset to certain task in OSWorld
        logger.info("Resetting environment...")
        logger.info("Switching task...")
        logger.info("Setting counters...")
        self._traj_no += 1
        self._step_no = 0
        self.action_history.clear()
        done = False
        for attempt in range(MAX_RETRIES):
            # Only revert to snapshot if environment has been used (step/setup)
            # This optimization is especially important for cloud providers like AWS
            # where unnecessary snapshot operations are costly and time-consuming
            
            task_use_proxy = False
            if task_config is not None:
                # Only consider task proxy requirement if proxy is enabled at system level
                task_use_proxy = task_config.get("proxy", False) and self.enable_proxy
                if not self.enable_proxy and task_config.get("proxy", False):
                    logger.info("Task requires proxy but proxy is disabled at system level, ignoring proxy requirement.")
                self.current_use_proxy = task_use_proxy
            
            if self.is_environment_used:
                logger.info("Environment has been used, reverting to snapshot {}...".format(self.snapshot_name))
                self._revert_to_snapshot()
                logger.info("Starting emulator...")
                self._start_emulator()
                logger.info("Emulator started.")
                # Reset the usage flag after reverting
                self.is_environment_used = False
            else:
                logger.info("Environment is clean, skipping snapshot revert (provider: {}).".format(self.provider_name))
                
            
            if task_config is not None:
                if task_use_proxy: #  If using proxy and proxy is enabled, set up the proxy configuration
                    self.setup_controller._proxy_setup(self.client_password) 
                
                # set task info
                self.task_id: str = task_config["id"]
                self.instruction = task_config["instruction"]
                self.config = task_config["config"] if "config" in task_config else []
                self.cache_dir: str = os.path.join(self.cache_dir_base, self.task_id)
                os.makedirs(self.cache_dir, exist_ok=True)
                self.setup_controller.reset_cache_dir(self.cache_dir)
                self._set_evaluator_info(task_config['evaluator'])

                logger.info("Setting up environment...")
                success = self.setup_controller.setup(self.config, task_use_proxy)

                # take out all register_action operations from config
                # and setup actions separately
                actions_to_register = []
                for op in self.config:
                    if op.get("type") == "register_action":
                        actions = op['parameters'].get("actions", []) or []
                        actions_to_register.extend(actions)
                if actions_to_register:
                    self._register_actions(actions_to_register)

                if success:
                    # Mark environment as used when setup is successfully executed
                    if self.config:  # Only mark as used if there were actual setup operations
                        self.is_environment_used = True
                    done = True
                else:
                    logger.error(
                        "Environment setup failed, retrying (%d/%d)...",
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    time.sleep(5)
            else:
                done = True

            if done:
                break
            
        logger.info("Environment setup complete.")

        if self.views:
            for view in self.views:
                view.close()
        self.views.clear()

        observation = self._get_obs()
        return observation

    def _set_evaluator_info(self, evaluator_config: Dict[str, Any]):
        """Set evaluator information from task config"""
        # evaluator dict
        # func -> metric function string, or list of metric function strings
        # conj -> conjunction of multiple metrics if func is a list with length > 1, "and"/"or"
        # result -> result getter config, or list of result getter configs
        # expected (optional) -> expected getter config, or list of expected getter configs
        # options (optional) -> metric options, or list of metric options
        # if func is a str list, then result, expected (if exists), options (if exists) should also be lists of the same length
        # even if one of the metrics does not need expected or options field, it should be included in the list with None
        self.evaluator = evaluator_config
        if not self.evaluator:
            return
        self.metric: Metric = [getattr(metrics, func) for func in self.evaluator["func"]] \
            if isinstance(self.evaluator["func"], list) \
            else getattr(metrics, self.evaluator["func"])
        self.metric_conj: str = self.evaluator.get("conj", "and")  # take conjunction of multiple metrics
        if "result" in self.evaluator and len(self.evaluator["result"]) > 0:
            self.result_getter: Getter = [getattr(getters, "get_{:}".format(res["type"])) for res in
                                          self.evaluator["result"]] \
                if isinstance(self.evaluator["result"], list) \
                else getattr(getters, "get_{:}".format(self.evaluator["result"]["type"]))
        else:
            self.result_getter = [None] * len(self.metric) \
                if isinstance(self.metric, list) \
                else None

        if "expected" in self.evaluator and len(self.evaluator["expected"]) > 0:
            self.expected_getter: Getter = [getattr(getters, "get_{:}".format(exp["type"])) if exp else None for exp in
                                            self.evaluator["expected"]] \
                if isinstance(self.evaluator["expected"], list) \
                else getattr(getters, "get_{:}".format(self.evaluator["expected"]["type"]))
        else:
            self.expected_getter = [None] * len(self.metric) \
                if isinstance(self.metric, list) \
                else None
        self.metric_options: Union[List[Dict[str, Any]], Dict[str, Any]] = [opt if opt else {} for opt in
                                                                            self.evaluator["options"]] \
            if isinstance(self.evaluator.get("options", {}), list) \
            else self.evaluator["options"] \
            if "options" in self.evaluator \
            else [{}] * len(self.metric) \
            if isinstance(self.metric, list) \
            else {}

        assert (not isinstance(self.evaluator["func"], list)
                or (len(self.metric) == len(self.result_getter) == len(self.expected_getter) == len(
                    self.metric_options)))
        
    def evaluate(self):
        """
        Evaluate whether the task is successfully completed.
        """
        if not self.evaluator:
            print("No evaluator defined for this task.")

        postconfig = self.evaluator.get("postconfig", [])
        self.setup_controller.setup(postconfig, self.enable_proxy)
        # Mark environment as used if there were postconfig setup operations
        if postconfig:
            self.is_environment_used = True

        if self.evaluator['func'] == "infeasible":
            if len(self.action_history) > 0 and self.action_history[-1] == "FAIL":
                return 1
            else:
                return 0
        else:
            if len(self.action_history) > 0 and self.action_history[-1] == "FAIL":
                return 0

        if type(self.metric) == list:
            # Multiple metrics to evaluate whether the task is successfully completed
            results = []
            assert len(self.metric) == len(self.result_getter), "The number of metrics and result getters must be the same"
            if "expected" in self.evaluator:
                assert len(self.metric) == len(self.expected_getter), "The number of metrics and expected getters must be the same"
            for idx, metric in enumerate(self.metric):
                try:
                    config = self.evaluator["result"][idx]
                    result_state = self.result_getter[idx](self, config)
                except FileNotFoundError:
                    logger.error("File not found!")
                    if self.metric_conj == 'and':
                        return 0

                if "expected" in self.evaluator and self.expected_getter and self.evaluator["expected"]:
                    expected_state = self.expected_getter[idx](self, self.evaluator["expected"][idx])
                    metric: int = metric(result_state, expected_state, **self.metric_options[idx])
                else:
                    metric: int = metric(result_state, **self.metric_options[idx])

                if self.metric_conj == 'and' and float(metric) == 0.0:
                    return 0
                elif self.metric_conj == 'or' and float(metric) == 1.0:
                    return 1
                else:
                    results.append(metric)

            return sum(results) / len(results) if self.metric_conj == 'and' else max(results)
        else:
            # Single metric to evaluate whether the task is successfully completed
            try:
                result_state = self.result_getter(self, self.evaluator["result"])
            except FileNotFoundError:
                logger.error("File not found!")
                return 0

            if "expected" in self.evaluator and self.expected_getter and self.evaluator["expected"]:
                expected_state = self.expected_getter(self, self.evaluator["expected"])
                metric: float = self.metric(result_state, expected_state, **self.metric_options)
            else:
                metric: float = self.metric(result_state, **self.metric_options)

        return metric

    def render(self, mode='rgb_array'):
        if mode == 'rgb_array':
            return self.controller.get_screenshot()
        else:
            raise ValueError('Unsupported render mode: {}'.format(mode))
