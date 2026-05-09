from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from agentvm.desktop_env import DesktopEnv


@dataclass(frozen=True)
class ActionScope:
    """Allowlist scope for a view. Empty/None means unrestricted for that dimension."""
    actions: Optional[Set[str]] = None       # allowlist by action identifier (action_type/name)
    bundles: Optional[Set[str]] = None       # allowlist by ActionBundle.name


COMPUTER13_ACTIONS = {
    'MOVE_TO', 'CLICK', 'MOUSE_DOWN', 'MOUSE_UP', 'RIGHT_CLICK', 'DOUBLE_CLICK',
    'DRAG_TO', 'SCROLL', 'TYPING', 'PRESS', 'KEY_DOWN', 'KEY_UP', 'HOTKEY'
}

SPECIAL_BUNDLES = {"computer_13", "pyautogui"}

class EnvView:
    """
    A thin, restrictive view over a live DesktopEnv that only allows a subset of actions.
    Effects apply to the same underlying VM; this is NOT a VM snapshot.
    """
    def __init__(
        self,
        env: "DesktopEnv",
        scope: ActionScope,
        name: Optional[str],
    ):
        self._env = env
        self._scope = scope
        self._name = name or "view"
        self._history: List[Dict[str, Any]] = []

        # Resolve bundles -> actions and build the final allowlist
        resolved_from_bundles = self._resolve_actions_for_bundles(scope.bundles or set())
        explicit_actions = scope.actions or set()
        self._allowed_actions: Set[str] = set(explicit_actions) | set(resolved_from_bundles)

        if scope.bundles:
            requested = set(scope.bundles)
            existing_bundle_names = {b.name for b in (self._env.action_bundles or [])}
            missing = requested - existing_bundle_names - SPECIAL_BUNDLES
            if missing:
                raise ValueError(f"EnvView '{self._name}': unknown bundle(s): {sorted(missing)}")

    def _resolve_actions_for_bundles(self, bundle_names: Set[str]) -> Set[str]:
        allowed: Set[str] = set()
        if not bundle_names:
            return allowed
        for bundle in (self._env.action_bundles or []):
            if bundle.name in bundle_names:
                for action in bundle.get_all_actions():
                    # action is a string
                    allowed.add(action)

        # explicitly add 'computer_13' if it is in the scope
        if "computer_13" in bundle_names:
            allowed.update(COMPUTER13_ACTIONS)
        # explicitly add 'pyautogui' if it is in the scope
        if "pyautogui" in bundle_names:
            allowed.update({"pyautogui"})
        return allowed

    def _normalize_action_type(self, action: Any) -> Optional[str]:
        # Strings are treated as raw pyautogui commands, except special control actions
        if isinstance(action, str):
            if action in {"WAIT", "FAIL", "DONE"}:
                return action
            return "pyautogui"
        if isinstance(action, dict):
            if "action_type" in action and action["action_type"]:
                return str(action["action_type"])
            if "name" in action and action["name"]:
                return str(action["name"])
            if "command" in action:
                return "pyautogui"
        return None

    def get_action_space(self) -> List[str]:
        return sorted(list(self._allowed_actions))

    def step(self, action: Any, pause: int = 2):
        """
        Validate action against allowlist; if allowed, delegate to underlying env.step.
        """
        act_type = self._normalize_action_type(action)

        # If an allowlist exists, enforce it (always allow special control actions)
        if self._allowed_actions:
            if act_type in {"WAIT", "FAIL", "DONE"}:
                # special control action
                return self._env.step(action, pause=pause)
            elif act_type in self._allowed_actions:
                # action is allowed
                return self._env.step(action, pause=pause)
            else:
                obs = {"error": f"Action '{act_type}' not allowed in view '{self._name}'"}
                return obs, 0, False, {"error": f"Action '{act_type}' not allowed in view '{self._name}'"}


    def close(self):
        # Does not close the underlying env; just clears local state
        pass

    @property
    def name(self) -> str:
        return self._name

    @property
    def allowed_actions(self) -> Set[str]:
        return set(self._allowed_actions)

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def get_action_space(self) -> List[str]:
        return sorted(list(self._allowed_actions))


class AppView(EnvView):
    """
    A view that is scoped to a single application window.

    - Action subset is defined by the provided scope (typically the app bundle).
    - Observations are the same as the underlying env, except that the
      `screenshot` field is replaced with a window-only screenshot captured via
      `import -window <WINDOW_ID> ...` inside the VM.
    """

    def __init__(
        self,
        env: "DesktopEnv",
        scope: ActionScope,
        name: Optional[str],
        app_name: str,
        window_id: str,
    ):
        super().__init__(env, scope, name or app_name)
        self._app_name = app_name
        self._window_id = window_id
        self._logger = logging.getLogger("desktopenv.appview")

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def window_id(self) -> str:
        return self._window_id

    def _capture_window_screenshot(self) -> Optional[bytes]:
        """
        Capture a screenshot of the bound window using ImageMagick `import`.

        Returns:
            The PNG bytes for the window screenshot, or None on failure.
        """
        # Use a deterministic, per-window temp path inside the VM.
        remote_path = f"/tmp/osworld_appview_{self._window_id}.png"
        script = f"import -window {self._window_id} {remote_path} || true\n"

        try:
            result = self._env.controller.run_bash_script(script, timeout=15)
            if not result:
                self._logger.error("Failed to run import for window screenshot (no result).")
                return None
        except Exception as exc:  # Defensive: network / VM issues should not crash the view.
            self._logger.error("Error while running import for window screenshot: %s", exc)
            return None

        try:
            data = self._env.controller.get_file(remote_path)
            if data is None:
                self._logger.error("Failed to fetch window screenshot from %s", remote_path)
            return data
        except Exception as exc:
            self._logger.error("Error while fetching window screenshot from %s: %s", remote_path, exc)
            return None

    def step(self, action: Any, pause: int = 2):
        """
        Same semantics as EnvView.step, but the screenshot in the returned
        observation is limited to this view's window.
        """
        result = super().step(action, pause=pause)

        # If the parent rejected the action, we just propagate the response.
        if not isinstance(result, tuple) or len(result) != 4:
            return result

        obs, reward, done, info = result
        if not isinstance(obs, dict):
            return obs, reward, done, info

        window_png = self._capture_window_screenshot()
        if window_png is not None:
            obs["screenshot"] = window_png

        return obs, reward, done, info