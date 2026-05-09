#!/usr/bin/env python3
"""
MCP Client library for calling MCP tools from action bundles.
This wraps the OsworldMcpClient and provides a command-line interface.
"""

import asyncio
import json
import sys
from typing import Any, Dict, Optional


class McpClient:
    """Client for calling MCP tools on the local MCP server."""
    
    config = {
        "mcpServers": {
            "osworld_mcp": {
                "url": "http://localhost:9292/mcp",
                "transport": "streamable-http"
            }
        }
    }
    
    @classmethod
    def call_tool(cls, name: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        Call an MCP tool by name with optional parameters.
        
        Args:
            name: The MCP tool name (e.g., 'libreoffice_calc.get_workbook_info')
            params: Dictionary of parameters for the tool
            
        Returns:
            String representation of the tool response
        """
        params = params or {}
        
        async def _call_tool():
            from fastmcp import Client
            client = Client(cls.config)
            async with client:
                response = await client.call_tool(name, params)
            return response
        
        try:
            response = asyncio.run(_call_tool())
            # Format the response for output
            if hasattr(response, 'content'):
                # Handle MCP response object
                result_parts = []
                for item in response.content:
                    if hasattr(item, 'text'):
                        result_parts.append(item.text)
                    else:
                        result_parts.append(str(item))
                return '\n'.join(result_parts)
            return str(response)
        except Exception as e:
            return f"Error calling MCP tool '{name}': {str(e)}"

    @classmethod
    def list_tools(cls, filter_prefix: Optional[str] = None) -> list:
        """
        List available MCP tools, optionally filtered by prefix.
        
        Args:
            filter_prefix: Optional prefix to filter tools (e.g., 'libreoffice_calc')
            
        Returns:
            List of tool definitions
        """
        async def _list_tools():
            from fastmcp import Client
            client = Client(cls.config)
            async with client:
                tool_list = await client.list_tools()
                return [{
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema
                } for tool in tool_list]
        
        try:
            tools = asyncio.run(_list_tools())
            if filter_prefix:
                tools = [t for t in tools if t['name'].startswith(filter_prefix)]
            return tools
        except Exception as e:
            print(f"Error listing MCP tools: {e}", file=sys.stderr)
            return []


def main():
    """Command-line entry point for calling MCP tools."""
    if len(sys.argv) < 2:
        print("Usage: mcp_client.py <tool_name> [--param1 value1] [--param2 value2] ...")
        print("       mcp_client.py --list [prefix]")
        sys.exit(1)
    
    if sys.argv[1] == '--list':
        prefix = sys.argv[2] if len(sys.argv) > 2 else None
        tools = McpClient.list_tools(prefix)
        print(json.dumps(tools, indent=2))
        return
    
    tool_name = sys.argv[1]
    
    # Parse remaining arguments as parameters
    params = {}
    i = 2
    while i < len(sys.argv):
        if sys.argv[i].startswith('--'):
            key = sys.argv[i][2:]  # Remove '--' prefix
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith('--'):
                value = sys.argv[i + 1]
                # Try to parse as JSON for complex types
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    pass  # Keep as string
                params[key] = value
                i += 2
            else:
                params[key] = True
                i += 1
        else:
            i += 1
    
    result = McpClient.call_tool(tool_name, params)
    print(result)


if __name__ == "__main__":
    main()
