"""
Utility functions for converting schemas to OpenAI function tool format.
"""

import yaml
from typing import Dict, Any, List, Optional, Union
from pathlib import Path


def get_function_calling_tool(
    name: str,
    docstring: str,
    arguments: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Converts command information into an OpenAI function calling tool definition.
    
    Args:
        name: The name of the function/command
        docstring: Description of what the function does
        arguments: List of argument dictionaries, each containing:
            - name: Argument name
            - type: Argument type (e.g., "string", "number", "boolean", "array")
            - description: Argument description
            - required: Whether the argument is required (bool)
            - enum: Optional list of allowed values
            - items: Optional items definition for array types
    
    Returns:
        Dict containing the OpenAI function schema
    
    Example:
        >>> tool = get_function_calling_tool(
        ...     name="text_web_browser",
        ...     docstring="Custom web browsing tool",
        ...     arguments=[
        ...         {
        ...             "name": "action",
        ...             "type": "string",
        ...             "description": "Action to perform",
        ...             "required": True,
        ...             "enum": ["visit_page", "page_up", "page_down"]
        ...         },
        ...         {
        ...             "name": "url",
        ...             "type": "string",
        ...             "description": "The URL to visit",
        ...             "required": False
        ...         }
        ...     ]
        ... )
    """
    tool = {
        "type": "function",
        "function": {
            "name": name,
            "description": docstring or "",
        },
    }
    
    properties = {}
    required = []
    
    if arguments:
        for arg in arguments:
            arg_name = arg.get("name")
            arg_type = arg.get("type", "string")
            arg_description = arg.get("description", "")
            
            properties[arg_name] = {
                "type": arg_type,
                "description": arg_description
            }
            
            # Handle items for array types
            if arg.get("items"):
                properties[arg_name]["items"] = arg["items"]
            
            # Handle enum if present
            if arg.get("enum"):
                properties[arg_name]["enum"] = arg["enum"]
            
            # Track required fields
            if arg.get("required"):
                required.append(arg_name)
    
    tool["function"]["parameters"] = {
        "type": "object",
        "properties": properties,
        "required": required
    }
    
    return tool


def parse_yaml_schema(yaml_input: Union[str, Path, Dict], strict: bool = False, additionalProperties: Union[bool, dict] = False) -> List[Dict[str, Any]]:
    """
    Parse a YAML schema file or dictionary and convert all actions to OpenAI function tool format.
    
    Args:
        yaml_input: Can be:
            - Path to a YAML file (str or Path object)
            - Dictionary already loaded from YAML
    
    Returns:
        List of dictionaries in OpenAI function tool schema format (one for each action)
    
    Example:
        >>> # From file
        >>> schemas = parse_yaml_schema("actions/text_web_browser/schema.yaml")
        >>> # Returns list of tool schemas
        >>> 
        >>> # From dict
        >>> yaml_dict = {...}
        >>> schemas = parse_yaml_schema(yaml_dict)
    """
    # Load YAML if it's a file path
    if isinstance(yaml_input, (str, Path)):
        with open(yaml_input, 'r') as f:
            yaml_data = yaml.safe_load(f)
    else:
        yaml_data = yaml_input
    
    # Extract all actions
    actions = yaml_data.get('actions', {})
    
    if not actions:
        raise ValueError("No actions found in YAML schema")
    
    # Convert all actions to tool schemas
    tool_schemas = []
    
    for action_name, action_data in actions.items():
        # Extract description from docstring
        description = action_data.get('docstring', '').strip()
        
        # Get arguments
        arguments = action_data.get('arguments', [])
        
        # Use get_function_calling_tool to create the schema
        tool_schema = get_function_calling_tool(
            name=action_name,
            docstring=description,
            arguments=arguments
        )
        if strict:
            tool_schema['function']['strict'] = strict
        if additionalProperties is not None:
            tool_schema['function']['parameters']['additionalProperties'] = additionalProperties
        else:
            additionalProperties = False
        tool_schemas.append(tool_schema)
    
    return tool_schemas

def convert_to_tool_schema(
    name: str,
    description: str,
    properties: Dict[str, Dict[str, Any]],
    required: Optional[List[str]] = None,
    strict: bool = True,
    additional_properties: bool = False
) -> Dict[str, Any]:
    """
    Convert a schema definition to OpenAI function tool format.
    
    Args:
        name: The name of the function/tool
        description: Description of what the function does
        properties: Dictionary of parameter properties. Each property should have:
            - type: The parameter type (e.g., "string", "number", "boolean")
            - description: Description of the parameter
            - enum (optional): List of allowed values
        required: List of required parameter names (default: None)
        strict: Whether to use strict mode (default: True)
        additional_properties: Whether to allow additional properties (default: False)
    
    Returns:
        Dictionary in OpenAI function tool schema format
    
    Example:
        >>> schema = convert_to_tool_schema(
        ...     name="terminate",
        ...     description="Submit the final answer to the task.",
        ...     properties={
        ...         "answer": {
        ...             "type": "string",
        ...             "description": "The final answer to be submitted."
        ...         },
        ...         "finish_reason": {
        ...             "type": "string",
        ...             "description": "The reason for finishing the task.",
        ...             "enum": ["success", "failure", "error"]
        ...         }
        ...     },
        ...     required=["answer", "finish_reason"]
        ... )
    """
    tool_schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": additional_properties
            },
            "strict": strict
        }
    }
    
    return tool_schema

def create_property(
    prop_type: str,
    description: str,
    enum: Optional[List[str]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a property definition for a schema.
    
    Args:
        prop_type: The type of the property (e.g., "string", "number", "boolean", "array", "object")
        description: Description of the property
        enum: Optional list of allowed values
        **kwargs: Additional property attributes (e.g., items, default, etc.)
    
    Returns:
        Dictionary representing the property definition
    
    Example:
        >>> prop = create_property(
        ...     prop_type="string",
        ...     description="The user's name",
        ...     enum=["alice", "bob"]
        ... )
    """
    property_def = {
        "type": prop_type,
        "description": description
    }
    
    if enum is not None:
        property_def["enum"] = enum
    
    # Add any additional attributes
    property_def.update(kwargs)
    
    return property_def

def merge_tool_schemas(schemas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge multiple tool schemas into a list.
    
    Args:
        schemas: List of tool schema dictionaries
    
    Returns:
        List of tool schemas
    
    Example:
        >>> schemas = merge_tool_schemas([schema1, schema2, schema3])
    """
    return schemas

# Example usage and predefined schemas
SUBMIT_ANSWER_TOOL_SCHEMA = convert_to_tool_schema(
    name="terminate",
    description="This marks the end of the task and it terminates the process. If the task is done/failed, call this tool to terminate the process and submit the final answer.",
    properties={
        "answer": {
            "type": "string",
            "description": "The final answer to be submitted. Leave empty if the task doesn't require a specific answer."
        },
        "finish_reason": {
            "type": "string",
            "enum": ["success", "failure", "error"],
            "description": "The reason for finishing the task."
        },
        "result_references": {
            "type": "string",
            "description": "The source/reference(s) to the result, if any. Use 'N/A' when not applicable."
        }
    },
    required=["finish_reason", "result_references"],
    strict=True,
    additional_properties=False
)


if __name__ == "__main__":
    import json
    
    # Example 1: Parse from YAML file
    print("=" * 60)
    print("Example 1: Parsing from YAML file")
    print("=" * 60)
    
    # Example YAML dict (simulating loaded YAML)
    yaml_dict = {
        "actions": {
            "text_web_browser": {
                "signature": "text_web_browser <action> [<url>] [<query>] [<download_path>]",
                "docstring": "Custom web browsing tool for navigating and searching through web pages in text format",
                "arguments": [
                    {
                        "name": "action",
                        "type": "string",
                        "description": "Action to perform in the web browser. Allowed options are: `visit_page`, `page_up`, `page_down`, `find_on_page`, `find_next`, `download`.",
                        "required": True,
                        "enum": ["visit_page", "page_up", "page_down", "find_on_page", "find_next", "download"]
                    },
                    {
                        "name": "url",
                        "type": "string",
                        "description": "The URL to visit or download. Required for `visit_page` and `download` actions.",
                        "required": False
                    },
                    {
                        "name": "query",
                        "type": "string",
                        "description": "Query to search for in the page. Required for `find_on_page` command.",
                        "required": False
                    }
                ]
            },
            "file_reader": {
                "signature": "file_reader <action> <path>",
                "docstring": "Read and display file contents",
                "arguments": [
                    {
                        "name": "action",
                        "type": "string",
                        "description": "Action to perform",
                        "required": True,
                        "enum": ["read", "list"]
                    },
                    {
                        "name": "path",
                        "type": "string",
                        "description": "File or directory path",
                        "required": True
                    }
                ]
            }
        }
    }
    
    schemas_from_yaml = parse_yaml_schema(yaml_dict)
    print(f"Parsed {len(schemas_from_yaml)} actions:\n")
    for i, schema in enumerate(schemas_from_yaml, 1):
        print(f"Action {i}: {schema['function']['name']}")
        print(json.dumps(schema, indent=2))
        print()
    
    # Example 2: Create a custom tool schema manually
    print("\n" + "=" * 60)
    print("Example 2: Creating schema manually")
    print("=" * 60)
    
    example_schema = convert_to_tool_schema(
        name="search_database",
        description="Search the database for records matching criteria.",
        properties={
            "query": create_property(
                prop_type="string",
                description="The search query string"
            ),
            "limit": create_property(
                prop_type="number",
                description="Maximum number of results to return"
            ),
            "category": create_property(
                prop_type="string",
                description="Category to filter by",
                enum=["books", "articles", "videos"]
            )
        },
        required=["query"],
        strict=True
    )
    
    print(json.dumps(example_schema, indent=2))
