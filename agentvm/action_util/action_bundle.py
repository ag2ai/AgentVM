from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Any

import yaml
from pydantic import BaseModel, model_validator, ConfigDict, Field

from agentvm.action_util.commands import Command, Argument


class ActionBundle(BaseModel):
    """Represents a bundle of actions that can be registered and executed.
    
    An action bundle consists of:
    - A directory containing the action implementation
    - A schema.yaml file defining the commands and their arguments
    - Optional bin/ directory with executable scripts
    - Optional install.sh for setup
    
    Attributes:
        local_path: Path to the action bundle directory
        remote_path: Path where the bundle is installed on the VM (optional)
        name: Name of the bundle (derived from directory name)
        commands: List of Command objects parsed from schema.yaml
    """
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    local_path: str
    remote_path: str | None = None
    name: str | None = None
    meta: Dict[str, Any] = Field(default_factory=dict)
    _commands: List[Command] | None = None
    
    @model_validator(mode="after")
    def validate_and_parse_schema(self) -> "ActionBundle":
        """Validate the bundle structure and parse the schema.yaml file."""
        # Convert to absolute path
        local_path = Path(self.local_path).expanduser().resolve()
        
        if not local_path.exists():
            msg = f"Bundle path '{local_path}' does not exist."
            raise ValueError(msg)
        
        # Check for schema.yaml
        schema_path = local_path / "schema.yaml"
        if not schema_path.exists():
            # Also try parent directory for backwards compatibility
            schema_path = local_path.parent / "schema.yaml"
            if not schema_path.exists():
                msg = f"schema.yaml not found in {local_path} or its parent directory."
                raise ValueError(msg)
            # Use parent directory as the actual bundle path
            local_path = local_path.parent
        
        # Update local_path to string
        self.local_path = str(local_path)
        
        # Set bundle name from directory name
        if self.name is None:
            self.name = local_path.name
        
        # Parse schema.yaml
        self._parse_schema(schema_path)
        
        return self
    
    def _parse_schema(self, schema_path: Path) -> None:
        """Parse the schema.yaml file and create Command objects.
        
        Args:
            schema_path: Path to the schema.yaml file
        """
        with open(schema_path, 'r') as f:
            schema_data = yaml.safe_load(f)
        
        if not schema_data or 'actions' not in schema_data:
            msg = f"Invalid schema.yaml at {schema_path}: missing 'actions' key"
            raise ValueError(msg)

        commands = []
        # Check for top-level meta first (e.g., native: true for system actions)
        bundle_meta: Dict[str, Any] | None = None
        if 'meta' in schema_data and isinstance(schema_data['meta'], dict):
            bundle_meta = schema_data['meta'].copy()

        # Track meta at bundle level if provided per-action
        inferred_meta: Dict[str, Any] | None = bundle_meta
        for cmd_name, cmd_config in schema_data['actions'].items():
            # Capture meta if present (e.g., related_app)
            if isinstance(cmd_config, dict) and 'meta' in cmd_config:
                cmd_meta = cmd_config.get('meta')
                if isinstance(cmd_meta, dict):
                    if inferred_meta is None:
                        inferred_meta = cmd_meta
                    else:
                        # Merge, preferring existing values
                        for k, v in cmd_meta.items():
                            inferred_meta.setdefault(k, v)
            # Parse arguments
            arguments = []
            if 'arguments' in cmd_config:
                for arg_config in cmd_config['arguments']:
                    arguments.append(Argument(
                        name=arg_config['name'],
                        type=arg_config['type'],
                        description=arg_config['description'],
                        required=arg_config['required'],
                        enum=arg_config.get('enum'),
                        items=arg_config.get('items'),
                        argument_format=arg_config.get('argument_format', '{{value}}')
                    ))
            
            # Create Command object
            command = Command(
                name=cmd_name,
                docstring=cmd_config.get('docstring'),
                signature=cmd_config.get('signature'),
                end_name=cmd_config.get('end_name'),
                arguments=arguments
            )
            commands.append(command)
        
        self._commands = commands
        if inferred_meta is not None:
            self.meta = inferred_meta
    
    @property
    def commands(self) -> List[Command]:
        """Get the list of commands in this bundle."""
        if self._commands is None:
            return []
        return self._commands
    
    @property
    def bundle_dir(self) -> str:
        """Get the bundle directory path for remote execution."""
        if self.remote_path:
            return self.remote_path
        # Default remote path on the VM
        return f"/home/user/actions/{self.name}"

    @property
    def is_native(self) -> bool:
        """Check if this bundle contains native Python actions.

        Native actions are implemented in PythonController methods rather than
        as bash scripts. They are indicated by 'native: true' in the bundle meta.
        """
        return self.meta.get("native", False) is True

    def get_command_by_name(self, name: str) -> Command | None:
        """Get a command by its name.
        
        Args:
            name: The command name to search for
            
        Returns:
            The Command object if found, None otherwise
        """
        for cmd in self.commands:
            if cmd.name == name:
                return cmd
        return None
    
    def has_action(self, action: str | dict) -> bool:
        """Check if an action exists in this bundle.
        
        Args:
            action: Either an action name (str) or action dict with "action_type" field
            
        Returns:
            True if the action exists in this bundle, False otherwise
        """
        action_name = action if isinstance(action, str) else action.get("action_type")
        if not action_name:
            return False
        
        return self.get_command_by_name(action_name) is not None
    
    def get_all_actions(self) -> List[str]:
        """Get all actions in this bundle."""
        return [cmd.name for cmd in self.commands]
    
    def get_execution_script(self, action: dict) -> str:
        """Generate a bash script to execute an action from this bundle.
        
        This method:
        1. Validates the action exists in this bundle
        2. Parses the action into a command string using the bundle's commands
        3. Constructs a bash script that:
           - Changes to the bundle directory
           - Activates virtual environment if it exists
           - Exports the bin directory to PATH
           - Executes the command
        
        Args:
            action: Dict containing "name" and optional "arguments" keys
            
        Returns:
            A bash script string ready to execute
            
        Raises:
            ValueError: If action is not in this bundle
        """
        from agentvm.action_util.util import parse_command
        
        action_name = action.get("action_type")
        if not action_name:
            raise ValueError("Action dict must contain 'action_type' field")
        
        if not self.has_action(action_name):
            raise ValueError(f"Action '{action_name}' not found in bundle '{self.name}'")
        
        # Parse the action into a command string using this bundle's commands
        command_str = parse_command(action, self.commands)        
        # Construct the bash script
        bundle_dir = self.bundle_dir
        bin_dir = f"{bundle_dir}/bin"
        venv_path = f"{bundle_dir}/.venv"
        
        # Build script with conditional venv activation
        # Extract the script name from command_str (first word)
        script_name = command_str.split()[0]
        script_path = f"{bin_dir}/{script_name}"
        # Replace script name with full path in command_str
        command_with_full_path = command_str.replace(script_name, script_path, 1)
        
        script = f"""set -euo pipefail
cd {bundle_dir}

VENV="{venv_path}"
ROOT_VENV="/home/user/actions/.venv"
PY311="/home/user/actions/python3.11/bin/python3"

if [ -x "$VENV/bin/python" ]; then
  "$VENV/bin/python" {command_with_full_path}
elif [ -x "$ROOT_VENV/bin/python" ]; then
  "$ROOT_VENV/bin/python" {command_with_full_path}
else
  "$PY311" {command_with_full_path}
fi
"""
        
        return script
    
    def __repr__(self) -> str:
        """String representation of the bundle."""
        return f"ActionBundle(name='{self.name}', commands={len(self.commands)})"
