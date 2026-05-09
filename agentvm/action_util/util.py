
import json
from shlex import quote
from typing import Any

from jinja2 import Template


def parse_command(model_response: dict, commands: list) -> str:
    """Parses the action from the model response.
    
    Args:
        model_response: Dict containing "name" and optional "arguments" keys
        commands: List of available Command objects
        
    Returns:
        Formatted command string ready for execution
        
    Raises:
        ValueError: If response format is invalid or command not found
    """
    if not isinstance(model_response, dict):
        raise ValueError("Model response must be a dictionary")
    if "action_type" not in model_response:
        raise ValueError("Model response missing 'action_type' field")
    
    command_name = model_response["action_type"]
    arguments = model_response.get("arguments", {})
    
    # Find the command definition
    commands_dict = {c.name: c for c in commands}
    command = commands_dict.get(command_name)
    
    if command is None:
        # If command not found, just join name with argument values
        if arguments:
            return " ".join([command_name, *arguments.values()])
        return command_name
    
    # Format arguments using their individual argument_format
    formatted_args = {}
    if command.arguments:
        for arg in command.arguments:
            if arg.name in arguments:
                value = arguments[arg.name]
                # Quote string values unless it's bash command or multi-line
                if isinstance(value, str) and command.name != "bash" and command.end_name is None:
                    value = quote(value)
                formatted_args[arg.name] = Template(arg.argument_format).render(value=value)
            else:
                # Provide empty string for optional arguments not supplied
                formatted_args[arg.name] = ""
    
    # Use the formatted arguments with invoke_format
    action = command.invoke_format.format(**formatted_args).strip()
    return action