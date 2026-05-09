import json
import logging
import random
import shlex
from typing import Any, Dict, Optional, List
from pathlib import Path
import time
import traceback
import requests

from agentvm.actions import KEYBOARD_KEYS
from agentvm.action_util.action_bundle import ActionBundle
from agentvm.app_env.base import AppEnv
from agentvm.app_env.registry import APP_ENV_REGISTRY

logger = logging.getLogger("desktopenv.pycontroller")


class PythonController:

    def __init__(self, vm_ip: str,
                 server_port: int,
                 pkgs_prefix: str = "import pyautogui; import time; pyautogui.FAILSAFE = False; {command}",
                 ):
        self.vm_ip = vm_ip
        self.http_server = f"http://{vm_ip}:{server_port}"
        self.pkgs_prefix = pkgs_prefix  # fixme: this is a hacky way to execute python commands. fix it and combine it with installation of packages
        self.retry_times = 3
        self.retry_interval = 5
        # App/window management state kept client-side for convenience
        self.software_config = self._load_software_config()
        logger.info(f"Loaded software config for apps: {list(self.software_config.keys())}")
        self.active_envs = {} # This maps the window id to the env that is currently active in that window
        self.state = { "current_window": None } # This is the window id of the current active window
        self.app_registry = {}

        # bundle registry, it should contain system, global, and app bundles.
        self.bundle_status = {
           "system": [],  # Native Python actions (e.g., open_app, close_window)
           "global": [],
           "app": []
        }
        # all bundles registered from env (used to bind bundles to app envs)
        self.available_bundles = []

        # Native action handlers - maps action_type to controller method
        self._native_action_handlers = {
            "open_app": self._handle_open_app,
            "close_window": self._handle_close_window,
            "switch_window": self._handle_switch_window,
        }

        # Auto-load system actions bundle
        self._load_system_actions()
    
    def _register_bundles(self, bundles: List[ActionBundle]):
        # assume we override available bundles
        # app bundles have the same name as the app name, we will get available overall app from registry.
        # global bundles are bundles that are not associated with any app
        self.available_bundles = bundles
        global_bundles = []
        app_bundles = []
        for bundle in bundles:
            if bundle.name in APP_ENV_REGISTRY:
                app_bundles.append(bundle)
            else:
                global_bundles.append(bundle)

        # Preserve system bundles, update global and app
        self.bundle_status["global"] = global_bundles
        self.bundle_status["app"] = app_bundles
        logger.info(f"Bundle status: {self.bundle_status}")

    def _load_system_actions(self):
        """Load the system_actions bundle which contains native Python actions."""
        system_actions_path = Path(__file__).resolve().parent.parent.parent / "actions" / "system_actions"
        if not system_actions_path.exists():
            logger.warning(f"System actions bundle not found at {system_actions_path}")
            return
        try:
            system_bundle = ActionBundle(local_path=str(system_actions_path))
            if system_bundle.is_native:
                self.bundle_status["system"] = [system_bundle]
                logger.info(f"Loaded system actions bundle: {system_bundle.get_all_actions()}")
            else:
                logger.warning("System actions bundle missing native flag, treating as global")
                self.bundle_status["global"].append(system_bundle)
        except Exception as e:
            logger.error(f"Failed to load system actions bundle: {e}")

    def _handle_open_app(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Native handler for open_app action."""
        app_name = params.get("app_name") or params.get("name")
        if not app_name:
            return {"status": "error", "error": "'app_name' required for open_app"}
        path = params.get("path")
        return self.open_app(app_name=app_name, path=path)

    def _handle_close_window(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Native handler for close_window action."""
        window_id = params.get("window_id")
        return self.close_window(window_id=window_id)

    def _handle_switch_window(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Native handler for switch_window action."""
        window_id = params.get("window_id")
        if not window_id:
            return {"status": "error", "error": "'window_id' required for switch_window"}
        return self.switch_window(window_id=window_id)

    @staticmethod
    def _is_valid_image_response(content_type: str, data: Optional[bytes]) -> bool:
        """Quick validation for PNG/JPEG payload using magic bytes; Content-Type is advisory.
        Returns True only when bytes look like a real PNG or JPEG.
        """
        if not isinstance(data, (bytes, bytearray)) or not data:
            return False
        # PNG magic
        if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
            return True
        # JPEG magic
        if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
            return True
        # If server explicitly marks as image, accept as a weak fallback (some environments strip magic)
        if content_type and ("image/png" in content_type or "image/jpeg" in content_type or "image/jpg" in content_type):
            return True
        return False

    def get_screenshot(self) -> Optional[bytes]:
        """
        Gets a screenshot from the server. With the cursor. None -> no screenshot or unexpected error.
        """

        for attempt_idx in range(self.retry_times):
            try:
                response = requests.get(self.http_server + "/screenshot", timeout=10)
                if response.status_code == 200:
                    content_type = response.headers.get("Content-Type", "")
                    content = response.content
                    if self._is_valid_image_response(content_type, content):
                        logger.info("Got screenshot successfully")
                        return content
                    else:
                        logger.error("Invalid screenshot payload (attempt %d/%d).", attempt_idx + 1, self.retry_times)
                        logger.info("Retrying to get screenshot.")
                else:
                    logger.error("Failed to get screenshot. Status code: %d", response.status_code)
                    logger.info("Retrying to get screenshot.")
            except Exception as e:
                logger.error("An error occurred while trying to get the screenshot: %s", e)
                logger.info("Retrying to get screenshot.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get screenshot.")
        return None

    def get_accessibility_tree(self) -> Optional[str]:
        """
        Gets the accessibility tree from the server. None -> no accessibility tree or unexpected error.
        """

        for _ in range(self.retry_times):
            try:
                response: requests.Response = requests.get(self.http_server + "/accessibility")
                if response.status_code == 200:
                    logger.info("Got accessibility tree successfully")
                    return response.json()["AT"]
                else:
                    logger.error("Failed to get accessibility tree. Status code: %d", response.status_code)
                    logger.info("Retrying to get accessibility tree.")
            except Exception as e:
                logger.error("An error occurred while trying to get the accessibility tree: %s", e)
                logger.info("Retrying to get accessibility tree.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get accessibility tree.")
        return None

    def get_terminal_output(self) -> Optional[str]:
        """
        Gets the terminal output from the server. None -> no terminal output or unexpected error.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.get(self.http_server + "/terminal")
                if response.status_code == 200:
                    logger.info("Got terminal output successfully")
                    return response.json()["output"]
                else:
                    logger.error("Failed to get terminal output. Status code: %d", response.status_code)
                    logger.info("Retrying to get terminal output.")
            except Exception as e:
                logger.error("An error occurred while trying to get the terminal output: %s", e)
                logger.info("Retrying to get terminal output.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get terminal output.")
        return None

    def get_file(self, file_path: str) -> Optional[bytes]:
        """
        Gets a file from the server.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/file", data={"file_path": file_path})
                if response.status_code == 200:
                    logger.info("File downloaded successfully")
                    return response.content
                else:
                    logger.error("Failed to get file. Status code: %d", response.status_code)
                    logger.info("Retrying to get file.")
            except Exception as e:
                logger.error("An error occurred while trying to get the file: %s", e)
                logger.info("Retrying to get file.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get file.")
        return None

    def get_folder(self, folder_path: str, dest_path: str) -> Dict[str, Any]:
        """
        Recursively downloads a folder from the VM to local destination.
        
        Args:
            folder_path: Absolute path to the folder on the VM
            dest_path: Local destination path where folder should be saved
            
        Returns:
            Dictionary with status, downloaded files count, failed files list, and error info
        """
        import os
        
        result = {
            "status": "success",
            "downloaded": 0,
            "failed": [],
            "total": 0
        }
        
        # Get directory tree
        dir_tree = self.get_vm_directory_tree(folder_path)
        if dir_tree is None:
            return {
                "status": "error",
                "error": f"Failed to get directory tree for {folder_path}",
                "downloaded": 0,
                "failed": [],
                "total": 0
            }
        
        # Check for error in directory tree response
        if "error" in dir_tree:
            return {
                "status": "error",
                "error": dir_tree["error"],
                "downloaded": 0,
                "failed": [],
                "total": 0
            }
        
        # Create destination folder
        os.makedirs(dest_path, exist_ok=True)
        logger.info(f"Downloading folder from VM: {folder_path} -> {dest_path}")
        
        # Recursively process directory tree
        # Directory tree format: {'type': 'directory', 'name': 'folder_name', 'children': [...]}
        # Children can be: {'type': 'file', 'name': 'file.txt'} or another directory dict
        def process_tree(tree: Dict[str, Any], vm_parent_path: str, local_parent_path: str):
            if tree.get("type") == "directory":
                # Process children of this directory
                children = tree.get("children", [])
                for child in children:
                    child_name = child.get("name")
                    if not child_name:
                        continue
                    
                    vm_path = os.path.join(vm_parent_path, child_name)
                    local_path = os.path.join(local_parent_path, child_name)
                    
                    if child.get("type") == "directory":
                        # Create directory and recurse
                        os.makedirs(local_path, exist_ok=True)
                        logger.info(f"Created directory: {local_path}")
                        process_tree(child, vm_path, local_path)
                    elif child.get("type") == "file":
                        # Download file
                        result["total"] += 1
                        file_content = self.get_file(vm_path)
                        if file_content is not None:
                            try:
                                with open(local_path, 'wb') as f:
                                    f.write(file_content)
                                result["downloaded"] += 1
                                logger.info(f"Downloaded: {vm_path} -> {local_path}")
                            except Exception as e:
                                logger.error(f"Failed to write file {local_path}: {e}")
                                result["failed"].append({"path": vm_path, "error": str(e)})
                        else:
                            logger.error(f"Failed to download file: {vm_path}")
                            result["failed"].append({"path": vm_path, "error": "Failed to download from VM"})
        
        try:
            # Start processing from the root directory
            process_tree(dir_tree, folder_path, dest_path)
            
            if result["failed"]:
                result["status"] = "partial"
                logger.warning(f"Folder download completed with {len(result['failed'])} failures out of {result['total']} files")
            else:
                logger.info(f"Folder download completed successfully: {result['downloaded']} files")
                
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            logger.error(f"Error during folder download: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        return result

    def execute_python_command(self, command: str) -> None:
        """
        Executes a python command on the server.
        It can be used to execute the pyautogui commands, or... any other python command. who knows?
        """
        # command_list = ["python", "-c", self.pkgs_prefix.format(command=command)]
        command_list = ["python", "-c", self.pkgs_prefix.format(command=command)]
        payload = json.dumps({"command": command_list, "shell": False})

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/execute", headers={'Content-Type': 'application/json'},
                                         data=payload, timeout=90)
                if response.status_code == 200:
                    logger.info("Command executed successfully: %s", response.text)
                    return response.json()
                else:
                    logger.error("Failed to execute command. Status code: %d", response.status_code)
                    logger.info("Retrying to execute command.")
            except requests.exceptions.ReadTimeout:
                break
            except Exception as e:
                logger.error("An error occurred while trying to execute the command: %s", e)
                logger.info("Retrying to execute command.")
            time.sleep(self.retry_interval)

        logger.error("Failed to execute command.")
        return None
    
    def run_python_script(self, script: str) -> Optional[Dict[str, Any]]:
        """
        Executes a python script on the server.
        """
        payload = json.dumps({"code": script})

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/run_python", headers={'Content-Type': 'application/json'},
                                         data=payload, timeout=90)
                if response.status_code == 200:
                    return response.json()
                else:
                    return {"status": "error", "message": "Failed to execute command.", "output": None, "error": response.json()["error"]}
            except requests.exceptions.ReadTimeout:
                break
            except Exception:
                logger.error("An error occurred while trying to execute the command: %s", traceback.format_exc())
                logger.info("Retrying to execute command.")
            time.sleep(self.retry_interval)

        logger.error("Failed to execute command.")
        return {"status": "error", "message": "Failed to execute command.", "output": "", "error": "Retry limit reached."}
    
    def run_bash_script(self, script: str, timeout: int = 30, working_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Executes a bash script on the server.
        
        :param script: The bash script content (can be multi-line)
        :param timeout: Execution timeout in seconds (default: 30)
        :param working_dir: Working directory for script execution (optional)
        :return: Dictionary with status, output, error, and returncode, or None if failed
        """
        payload = json.dumps({
            "script": script,
            "timeout": timeout,
            "working_dir": working_dir
        })

        for _ in range(self.retry_times):
            try:
                response = requests.post(
                    self.http_server + "/run_bash_script", 
                    headers={'Content-Type': 'application/json'},
                    data=payload, 
                    timeout=timeout + 100  # Add buffer to HTTP timeout
                )
                if response.status_code == 200:
                    result = response.json()
                    logger.info("Bash script executed successfully with return code: %d", result.get("returncode", -1))
                    return result
                else:
                    logger.error("Failed to execute bash script. Status code: %d, response: %s", 
                                response.status_code, response.text)
                    logger.info("Retrying to execute bash script.")
            except requests.exceptions.ReadTimeout:
                logger.error("Bash script execution timed out")
                return {
                    "status": "error",
                    "output": "",
                    "error": f"Script execution timed out after {timeout} seconds",
                    "returncode": -1
                }
            except Exception as e:
                logger.error("An error occurred while trying to execute the bash script: %s", e)
                logger.info("Retrying to execute bash script.")
            time.sleep(self.retry_interval)

        logger.error("Failed to execute bash script after %d retries.", self.retry_times)
        return {
            "status": "error",
            "output": "",
            "error": f"Failed to execute bash script after {self.retry_times} retries",
            "returncode": -1
        }

    def get_windows_status(self) -> Dict[str, Any]:
        """
        Returns current window status using wmctrl/xprop.
        {
          "windows": { win_id: { "wm_class": str, "title": str, "desktop": str } },
          "active_window_id": str | None
        }
        """
        status: Dict[str, Any] = {"windows": {}, "active_window_id": None}

        # List windows
        list_result = self.run_bash_script("wmctrl -lx || true", timeout=10)
        if list_result and list_result.get("output"):
            lines = [ln for ln in list_result["output"].splitlines() if ln.strip()]
            for ln in lines:
                # wmctrl -lx format: ID DESKTOP WM_CLASS HOST TITLE...
                parts = ln.split(None, 4)
                if len(parts) >= 5:
                    win_id = parts[0]
                    desktop = parts[1]
                    wm_class = parts[2]
                    title = parts[4]
                elif len(parts) >= 4:
                    win_id = parts[0]
                    desktop = parts[1]
                    wm_class = parts[2]
                    title = parts[3]
                else:
                    continue

                # Filter out background runtime windows
                if wm_class.lower().startswith("gjs."):
                    continue

                status["windows"][win_id] = {
                    "wm_class": wm_class,
                    "title": title,
                    "desktop": desktop,
                }

        # Active window id using xdotool
        active_result = self.run_bash_script(
            "xdotool getactivewindow 2>/dev/null | xargs printf '0x%08x' || true",
            timeout=5,
        )
        if active_result and active_result.get("output"):
            active_id = active_result["output"].strip()
            status["active_window_id"] = active_id if active_id else None

        return status

    def switch_window(self, window_id: str) -> Dict[str, Any]:
        """
        Focus a window by window_id using wmctrl, update current window state,
        and ensure an AppEnv exists for the focused window.

        Returns: { status, window_id, wm_class, title }
        """
        # Get current windows to validate window_id exists and get metadata
        windows_status = self.get_windows_status()
        windows = windows_status.get("windows", {})
        
        if window_id not in windows:
            return {"status": "error", "message": f"Window {window_id} not found"}
        
        meta = windows[window_id]

        act_script = (
            f"wmctrl -i -a {window_id} || true\n"
            "sleep 1\n"
            f"wmctrl -i -r {window_id} -b add,fullscreen || true\n"
        )
        _ = self.run_bash_script(act_script, timeout=10)

        self.state["current_window"] = window_id
        
        # Create AppEnv if not exists - determine app_name from wm_class
        if window_id not in self.active_envs:
            wm_class = meta.get("wm_class", "")
            # Try to find matching app from software_config
            app_name = None
            for name, config in self.software_config.items():
                hint = config.get("wm_class_hint", "").lower()
                if hint and hint in wm_class.lower():
                    app_name = name
                    break
            
            if app_name:
                env = self.create_app_env(app_name)
                self.active_envs[window_id] = env

        logger.info(f"Switched to window {window_id}")
        return {
            "status": "success",
            "window_id": window_id,
            "wm_class": meta.get("wm_class", ""),
            "title": meta.get("title", ""),
        }

    def execute_registered_action(self, action: Dict[str, Any], timeout: int = 60) -> Optional[Dict[str, Any]]:
        """
        Execute a registered action. Checks bundles in order: system -> global -> app.

        System bundles contain native Python actions (e.g., open_app, close_window).
        Global bundles are bash-based actions available everywhere.
        App bundles are bash-based actions tied to specific applications.
        """
        name = action.get("action_type")
        params = action.get("arguments", {})

        # Check system bundles first (native Python actions)
        for bundle in self.bundle_status.get("system", []):
            if bundle.has_action(action):
                handler = self._native_action_handlers.get(name)
                if handler:
                    logger.info(f"Executing native action: {name}")
                    return handler(params)
                else:
                    logger.warning(f"Native action {name} found in bundle but no handler registered")

        # Check if the action is in any of the global bundles
        for bundle in self.bundle_status['global']:
            if bundle.has_action(action):
                script = bundle.get_execution_script(action)
                logger.info(f"Executing global bundle {bundle.name}...")
                return self.run_bash_script(script, timeout=timeout, working_dir=bundle.bundle_dir)

        # App-specific action: delegate to bound env if available (uses registered bundles)
        # FIXME: Current design requires the action to be in the available actions of the current window's env.
        # FIXME: Routing the action to the correct env is not hard and can be done by using if-else.
        cur_window_id = self.state.get("current_window")
        cur_env = self.active_envs.get(cur_window_id)
        if cur_env and cur_env.has_action(action):
            return cur_env.step(action)
        return {"status": "error", "error": f"Action {name} not found in env."}

    def get_action_space(self) -> List[str]:
        """Get all available actions: system + global + current app's actions."""
        action_space = []
        # System actions (native Python actions like open_app, close_window)
        for bundle in self.bundle_status.get("system", []):
            action_space.extend(bundle.get_all_actions())
        # Global bundles
        for bundle in self.bundle_status['global']:
            action_space.extend(bundle.get_all_actions())
        # Current window's app-specific actions
        cur_window_id = self.state.get("current_window")
        cur_env = self.active_envs.get(cur_window_id)
        if cur_env:
            action_space.extend(cur_env.get_all_actions())
        return action_space
    
    # --- App management ---
    def _load_software_config(self) -> Dict[str, Dict[str, Any]]:
        """Load software config from controllers/software_config.json only."""
        path = Path(__file__).resolve().parent / "software_config.json"
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # Keep only dict entries; user fully controls content
                return {k: v for k, v in data.items() if isinstance(v, dict)}
        except Exception as e:
            logger.warning("Failed to load software_config.json at %s: %s", path, e)
        return {}

    def _list_windows_by_wm_class_hint(self, wm_class_hint: str) -> Dict[str, Dict[str, str]]:
        """
        Return mapping win_id -> {wm_class, title, desktop} for windows whose wm_class contains the hint (case-insensitive).
        """
        result: Dict[str, Dict[str, str]] = {}
        resp = self.run_bash_script("wmctrl -lx || true\n", timeout=10)
        if not resp or not resp.get("output"):
            return result
        hint = (wm_class_hint or "").lower()
        for ln in resp["output"].splitlines():
            parts = ln.split(None, 4)
            if len(parts) >= 5:
                win_id = parts[0]
                desktop = parts[1]
                wm_class = parts[2]
                title = parts[4]
                # Filter out background runtime windows
                if wm_class.lower().startswith("gjs."):
                    continue
                if not hint or hint in wm_class.lower():
                    result[win_id] = {"wm_class": wm_class, "title": title, "desktop": desktop}
        return result

    def _snapshot_app_window_ids(self, app_name: str, wm_class_hint: str) -> set:
        baseline_ids = set()
        if app_name in self.app_registry:
            baseline_ids.update(self.app_registry[app_name].get("windows", {}).keys())
        baseline_ids.update(self._list_windows_by_wm_class_hint(wm_class_hint).keys())
        return baseline_ids

    def open_app(self, app_name: str, path: Optional[str] = None, timeout: int = 20) -> Dict[str, Any]:
        """
        Launch an app via bash, wait for a window to appear, activate and maximize it,
        update controller registry, and return window metadata.

        Args:
            app_name: Name of the application to launch (must be in software_config.json)
            path: Optional file or folder path to open with the app
            timeout: Maximum seconds to wait for window to appear

        Returns: { status, window_id, wm_class, title }
        """
        entry = self.software_config.get(app_name)
        if not entry or not entry.get("launch"):
            return {"status": "error", "error": f"Unknown app '{app_name}' or missing launch command"}

        # Validate path exists if provided
        if path:
            check_script = f"test -e {shlex.quote(path)} && echo 'exists' || echo 'not_found'"
            check_result = self.run_bash_script(check_script, timeout=5)
            if not check_result or check_result.get("output", "").strip() != "exists":
                return {"status": "error", "error": f"Path does not exist: {path}"}

        launch_cmd = entry["launch"].strip()
        if path:
            launch_cmd = f"{launch_cmd} {shlex.quote(path)}"

        # Snapshot existing windows for this app before launching
        wm_hint = (entry.get("wm_class_hint") or "").strip()
        baseline_ids = self._snapshot_app_window_ids(app_name, wm_hint)

        # Launch in background and print the PID
        script = f"( {launch_cmd} ) >/dev/null 2>&1 & echo $!\n"
        launch_result = self.run_bash_script(script, timeout=10)
        if not launch_result or launch_result.get("status") != "success":
            return {"status": "error", "error": (launch_result or {}).get("output", "failed to launch")}

        pid_line = (launch_result.get("output") or "").strip().splitlines()[-1:] or [""]
        try:
            pid = int(pid_line[0]) if pid_line[0] else None
        except Exception:
            pid = None

        # Poll for a newly added window (difference from baseline);
        # fallback to PID if needed.
        win_id: Optional[str] = None
        start = time.time()
        while time.time() - start < timeout:
            current_map = self._list_windows_by_wm_class_hint(wm_hint)
            current_ids = list(current_map.keys())
            new_ids = [wid for wid in current_ids if wid not in baseline_ids]
            if new_ids:
                win_id = new_ids[0]
            elif pid is not None:
                find_script = f"wmctrl -lp | awk '$3=={pid} {{print $1}}' || true\n"
                find_result = self.run_bash_script(find_script, timeout=5)
                if find_result and find_result.get("output"):
                    candidates = [ln.strip() for ln in find_result["output"].splitlines() if ln.strip()]
                    if candidates:
                        pref = [c for c in candidates if c not in baseline_ids]
                        win_id = pref[0] if pref else candidates[0]

            if win_id:
                break
            time.sleep(0.5)

        if not win_id:
            return {"status": "error", "error": f"No window found for app '{app_name}'"}

        # Activate and maximize the window
        act_script = (
            f"wmctrl -i -a {win_id} || true\n"
            "sleep 1\n"
            f"wmctrl -i -r {win_id} -b add,fullscreen || true\n"
        )
        _ = self.run_bash_script(act_script, timeout=10)

        # Fetch window details via wmctrl -lx
        info_script = f"wmctrl -lx | awk '$1==\"{win_id}\" {{print $0}}' || true\n"
        info = self.run_bash_script(info_script, timeout=5)
        wm_class = ""
        title = ""
        desktop = ""
        if info and info.get("output"):
            line = info["output"].strip().splitlines()[0]
            parts = line.split(None, 4)
            if len(parts) >= 5:
                desktop = parts[1]
                wm_class = parts[2]
                title = parts[4]
        
        self.state["current_window"] = win_id

        # Create and register the env for the app
        # env = self.create_app_env(app_name)
        # self.active_envs[win_id] = env

        return {
            "status": "success",
            "window_id": win_id,
            "wm_class": wm_class,
            "title": title,
        }
    
    def close_window(self, window_id: Optional[str] = None) -> Dict[str, Any]:
        """Close a window by window_id. If window_id is not provided, close the current window.
        
        Args:
            window_id: Optional window ID to close. If None, closes current window.
            
        Returns:
            Dictionary with status and message
        """
        # Use provided window_id or fall back to current window
        win_id = window_id if window_id is not None else self.state.get("current_window")
        
        if not win_id:
            return {"status": "error", "message": "No window to close"}
        
        # Close the env if it exists
        if win_id in self.active_envs:
            logger.info(f"Closing window {win_id}")
            self.active_envs[win_id].close()
            del self.active_envs[win_id]
        else:
            logger.warning(f"No active env for window {win_id}")
        
        # Close the window using wmctrl
        result = self.run_bash_script(f"wmctrl -i -c {win_id} || true\n", timeout=10)
        logger.info(f"Window {win_id} closed successfully")
        
        # Clear current_window if we just closed it
        if self.state.get("current_window") == win_id:
            self.state["current_window"] = None
        
        return {"status": "success", "window_id": win_id}
        

    def create_app_env(self, app_name: str):
        """Instantiate an AppEnv for the given app and current window.

        Looks up a specific env class from APP_ENV_REGISTRY; falls back to base AppEnv.
        """
        win_id = self.state.get("current_window")
        if app_name not in APP_ENV_REGISTRY:
            logger.warning(f"Unknown app '{app_name}'")
            EnvCls = AppEnv
        else:
            EnvCls = APP_ENV_REGISTRY.get(app_name)
        # get the bundles to register for the app, by default the bundle names should be the same as the app name
        bundles = [bundle for bundle in self.available_bundles if bundle.meta.get("related_app") == app_name]
        return EnvCls(self, app_name, window_id=win_id, bundles=bundles)  # type: ignore

    def get_state(self) -> str:
        return self.state

    def execute_action(self, action: Dict[str, Any]):
        """
        Executes an action on the server computer.
        """
        if action in ['WAIT', 'FAIL', 'DONE']:
            return

        action_type = action["action_type"]
        parameters = action["arguments"] if "arguments" in action else {param: action[param] for param in action if param != 'action_type'}
        move_mode = random.choice(
            ["pyautogui.easeInQuad", "pyautogui.easeOutQuad", "pyautogui.easeInOutQuad", "pyautogui.easeInBounce",
             "pyautogui.easeInElastic"])
        duration = random.uniform(0.5, 1)

        if action_type == "MOVE_TO":
            if parameters == {} or None:
                self.execute_python_command("pyautogui.moveTo()")
            elif "x" in parameters and "y" in parameters:
                x = parameters["x"]
                y = parameters["y"]
                self.execute_python_command(f"pyautogui.moveTo({x}, {y}, {duration}, {move_mode})")
            else:
                raise Exception(f"Unknown parameters: {parameters}")

        elif action_type == "CLICK":
            if parameters == {} or None:
                self.execute_python_command("pyautogui.click()")
            elif "button" in parameters and "x" in parameters and "y" in parameters:
                button = parameters["button"]
                x = parameters["x"]
                y = parameters["y"]
                if "num_clicks" in parameters:
                    num_clicks = parameters["num_clicks"]
                    self.execute_python_command(
                        f"pyautogui.click(button='{button}', x={x}, y={y}, clicks={num_clicks})")
                else:
                    self.execute_python_command(f"pyautogui.click(button='{button}', x={x}, y={y})")
            elif "button" in parameters and "x" not in parameters and "y" not in parameters:
                button = parameters["button"]
                if "num_clicks" in parameters:
                    num_clicks = parameters["num_clicks"]
                    self.execute_python_command(f"pyautogui.click(button='{button}', clicks={num_clicks})")
                else:
                    self.execute_python_command(f"pyautogui.click(button='{button}')")
            elif "button" not in parameters and "x" in parameters and "y" in parameters:
                x = parameters["x"]
                y = parameters["y"]
                if "num_clicks" in parameters:
                    num_clicks = parameters["num_clicks"]
                    self.execute_python_command(f"pyautogui.click(x={x}, y={y}, clicks={num_clicks})")
                else:
                    self.execute_python_command(f"pyautogui.click(x={x}, y={y})")
            else:
                raise Exception(f"Unknown parameters: {parameters}")

        elif action_type == "MOUSE_DOWN":
            if parameters == {} or None:
                self.execute_python_command("pyautogui.mouseDown()")
            elif "button" in parameters:
                button = parameters["button"]
                self.execute_python_command(f"pyautogui.mouseDown(button='{button}')")
            else:
                raise Exception(f"Unknown parameters: {parameters}")

        elif action_type == "MOUSE_UP":
            if parameters == {} or None:
                self.execute_python_command("pyautogui.mouseUp()")
            elif "button" in parameters:
                button = parameters["button"]
                self.execute_python_command(f"pyautogui.mouseUp(button='{button}')")
            else:
                raise Exception(f"Unknown parameters: {parameters}")

        elif action_type == "RIGHT_CLICK":
            if parameters == {} or None:
                self.execute_python_command("pyautogui.rightClick()")
            elif "x" in parameters and "y" in parameters:
                x = parameters["x"]
                y = parameters["y"]
                self.execute_python_command(f"pyautogui.rightClick(x={x}, y={y})")
            else:
                raise Exception(f"Unknown parameters: {parameters}")

        elif action_type == "DOUBLE_CLICK":
            if parameters == {} or None:
                self.execute_python_command("pyautogui.doubleClick()")
            elif "x" in parameters and "y" in parameters:
                x = parameters["x"]
                y = parameters["y"]
                self.execute_python_command(f"pyautogui.doubleClick(x={x}, y={y})")
            else:
                raise Exception(f"Unknown parameters: {parameters}")

        elif action_type == "DRAG_TO":
            if "x" in parameters and "y" in parameters:
                x = parameters["x"]
                y = parameters["y"]
                self.execute_python_command(
                    f"pyautogui.dragTo({x}, {y}, duration=1.0, button='left', mouseDownUp=True)")

        elif action_type == "SCROLL":
            # todo: check if it is related to the operating system, as https://github.com/TheDuckAI/DuckTrack/blob/main/ducktrack/playback.py pointed out
            if "dx" in parameters and "dy" in parameters:
                dx = parameters["dx"]
                dy = parameters["dy"]
                self.execute_python_command(f"pyautogui.hscroll({dx})")
                self.execute_python_command(f"pyautogui.vscroll({dy})")
            elif "dx" in parameters and "dy" not in parameters:
                dx = parameters["dx"]
                self.execute_python_command(f"pyautogui.hscroll({dx})")
            elif "dx" not in parameters and "dy" in parameters:
                dy = parameters["dy"]
                self.execute_python_command(f"pyautogui.vscroll({dy})")
            else:
                raise Exception(f"Unknown parameters: {parameters}")

        elif action_type == "TYPING":
            if "text" not in parameters:
                raise Exception(f"Unknown parameters: {parameters}")
            # deal with special ' and \ characters
            # text = parameters["text"].replace("\\", "\\\\").replace("'", "\\'")
            # self.execute_python_command(f"pyautogui.typewrite('{text}')")
            text = parameters["text"]
            self.execute_python_command("pyautogui.typewrite({:})".format(repr(text)))

        elif action_type == "PRESS":
            if "key" not in parameters:
                raise Exception(f"Unknown parameters: {parameters}")
            key = parameters["key"]
            if key.lower() not in KEYBOARD_KEYS:
                raise Exception(f"Key must be one of {KEYBOARD_KEYS}")
            self.execute_python_command(f"pyautogui.press('{key}')")

        elif action_type == "KEY_DOWN":
            if "key" not in parameters:
                raise Exception(f"Unknown parameters: {parameters}")
            key = parameters["key"]
            if key.lower() not in KEYBOARD_KEYS:
                raise Exception(f"Key must be one of {KEYBOARD_KEYS}")
            self.execute_python_command(f"pyautogui.keyDown('{key}')")

        elif action_type == "KEY_UP":
            if "key" not in parameters:
                raise Exception(f"Unknown parameters: {parameters}")
            key = parameters["key"]
            if key.lower() not in KEYBOARD_KEYS:
                raise Exception(f"Key must be one of {KEYBOARD_KEYS}")
            self.execute_python_command(f"pyautogui.keyUp('{key}')")

        elif action_type == "HOTKEY":
            if "keys" not in parameters:
                raise Exception(f"Unknown parameters: {parameters}")
            keys = parameters["keys"]
            if not isinstance(keys, list):
                raise Exception("Keys must be a list of keys")
            for key in keys:
                if key.lower() not in KEYBOARD_KEYS:
                    raise Exception(f"Key must be one of {KEYBOARD_KEYS}")

            keys_para_rep = "', '".join(keys)
            self.execute_python_command(f"pyautogui.hotkey('{keys_para_rep}')")
        
        elif action_type in ['WAIT', 'FAIL', 'DONE']:
            pass

        else:
            raise Exception(f"Unknown action type: {action_type}")

    # Record video
    def start_recording(self):
        """
        Starts recording the screen.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/start_recording")
                if response.status_code == 200:
                    logger.info("Recording started successfully")
                    return
                else:
                    logger.error("Failed to start recording. Status code: %d", response.status_code)
                    logger.info("Retrying to start recording.")
            except Exception as e:
                logger.error("An error occurred while trying to start recording: %s", e)
                logger.info("Retrying to start recording.")
            time.sleep(self.retry_interval)

        logger.error("Failed to start recording.")

    def end_recording(self, dest: str):
        """
        Ends recording the screen.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/end_recording")
                if response.status_code == 200:
                    logger.info("Recording stopped successfully")
                    with open(dest, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                    return
                else:
                    logger.error("Failed to stop recording. Status code: %d", response.status_code)
                    logger.info("Retrying to stop recording.")
            except Exception as e:
                logger.error("An error occurred while trying to stop recording: %s", e)
                logger.info("Retrying to stop recording.")
            time.sleep(self.retry_interval)

        logger.error("Failed to stop recording.")

    # Additional info
    def get_vm_platform(self):
        """
        Gets the size of the vm screen.
        """
        return self.execute_python_command("import platform; print(platform.system())")['output'].strip()

    def get_vm_screen_size(self):
        """
        Gets the size of the vm screen.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/screen_size")
                if response.status_code == 200:
                    logger.info("Got screen size successfully")
                    return response.json()
                else:
                    logger.error("Failed to get screen size. Status code: %d", response.status_code)
                    logger.info("Retrying to get screen size.")
            except Exception as e:
                logger.error("An error occurred while trying to get the screen size: %s", e)
                logger.info("Retrying to get screen size.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get screen size.")
        return None

    def get_vm_window_size(self, app_class_name: str):
        """
        Gets the size of the vm app window.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/window_size", data={"app_class_name": app_class_name})
                if response.status_code == 200:
                    logger.info("Got window size successfully")
                    return response.json()
                else:
                    logger.error("Failed to get window size. Status code: %d", response.status_code)
                    logger.info("Retrying to get window size.")
            except Exception as e:
                logger.error("An error occurred while trying to get the window size: %s", e)
                logger.info("Retrying to get window size.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get window size.")
        return None

    def get_vm_wallpaper(self):
        """
        Gets the wallpaper of the vm.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/wallpaper")
                if response.status_code == 200:
                    logger.info("Got wallpaper successfully")
                    return response.content
                else:
                    logger.error("Failed to get wallpaper. Status code: %d", response.status_code)
                    logger.info("Retrying to get wallpaper.")
            except Exception as e:
                logger.error("An error occurred while trying to get the wallpaper: %s", e)
                logger.info("Retrying to get wallpaper.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get wallpaper.")
        return None

    def get_vm_desktop_path(self) -> Optional[str]:
        """
        Gets the desktop path of the vm.
        """

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/desktop_path")
                if response.status_code == 200:
                    logger.info("Got desktop path successfully")
                    return response.json()["desktop_path"]
                else:
                    logger.error("Failed to get desktop path. Status code: %d", response.status_code)
                    logger.info("Retrying to get desktop path.")
            except Exception as e:
                logger.error("An error occurred while trying to get the desktop path: %s", e)
                logger.info("Retrying to get desktop path.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get desktop path.")
        return None

    def get_vm_directory_tree(self, path) -> Optional[Dict[str, Any]]:
        """
        Gets the directory tree of the vm.
        """
        payload = json.dumps({"path": path})

        for _ in range(self.retry_times):
            try:
                response = requests.post(self.http_server + "/list_directory", headers={'Content-Type': 'application/json'}, data=payload)
                if response.status_code == 200:
                    logger.info("Got directory tree successfully")
                    return response.json()["directory_tree"]
                else:
                    logger.error("Failed to get directory tree. Status code: %d", response.status_code)
                    logger.info("Retrying to get directory tree.")
            except Exception as e:
                logger.error("An error occurred while trying to get directory tree: %s", e)
                logger.info("Retrying to get directory tree.")
            time.sleep(self.retry_interval)

        logger.error("Failed to get directory tree.")
        return None
