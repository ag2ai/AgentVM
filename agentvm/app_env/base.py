from typing import Any, Dict, List, Optional, Union

from agentvm.action_util.action_bundle import ActionBundle

class AppEnv:
    def __init__(self, controller, app_name: str, window_id: Optional[str] = None, bundles: Optional[List[ActionBundle]] = None):
        self.controller = controller
        self.app_name = app_name
        self.window_id: Optional[str] = window_id
        self.state = {}
        self.available_bundles = bundles or []
        self.available_actions_names: List[str] = [
            action for bundle in self.available_bundles for action in bundle.get_all_actions()
        ]
        
    def has_action(self, action: Union[str, dict]) -> bool:
        action_name = action if isinstance(action, str) else action.get("action_type") or action.get("name")
        if not action_name:
            return False
        return action_name in self.available_actions_names

    def get_all_actions(self) -> List[str]:
        return list(self.available_actions_names)

    def get_obs(self) -> Dict[str, Any]:
        pass

    def close(self) -> Dict[str, Any]:
        pass
    
    def step(self, action: Dict[str, Any]) -> Dict[str, Any]:
        for bundle in self.available_bundles:
            if bundle.has_action(action):
                script = bundle.get_execution_script(action)
                result = self.controller.run_bash_script(script, timeout=60, working_dir=bundle.bundle_dir)
                return result
        return {"status": "error", "message": "Action not found in any bundle"}