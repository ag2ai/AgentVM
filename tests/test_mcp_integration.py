#!/usr/bin/env python3
"""
Minimal smoke test for the unified MCP action bundles.

This registers the generated mcp_* bundles, boots the environment, and calls a couple
of zero-argument sub-actions to verify the glue layer end-to-end.

Replace `provider_name` / `path_to_vm` with your setup before running.
"""

from agentvm.desktop_env import DesktopEnv
import logging

logging.basicConfig(level=logging.INFO)


def main() -> None:
    setup_config = [
        {
            "type": "upload_file",
            "parameters": {
                "files": [
                    {"local_path": "/home/jialel/osworld/agentvm/server/main.py", "path": "/home/user/server/main.py"},
                ]
            },
        },
        {
            "type": "register_action",
            "parameters": {
                "actions": [
                    {"local_path": "actions/mcp_libreoffice_calc"},
                    {"local_path": "actions/mcp_code"},
                    {"local_path": "actions/mcp_google_chrome"},
                    {"local_path": "actions/mcp_libreoffice_impress"},
                    {"local_path": "actions/mcp_libreoffice_writer"},
                    {"local_path": "actions/mcp_vlc"},
                ]
            },
        }
    ]

    env = DesktopEnv(
        provider_name="docker",          # TODO: set to your provider
        path_to_vm="docker_vm_data/Ubuntu.qcow2",    # TODO: set to your VM path
        enable_mcp=True,                 # ensures MCP server + soffice launcher
    )

    try:
        env.reset(setup_config=setup_config)

        # 1) Calc bundle: adjust_column_width
        calc_action = {"action_type": "libreoffice_calc", "arguments": {"action": "adjust_column_width", "columns": "A:B"}}
        obs, _, _, _ = env.step(calc_action)
        print("Calc env_info output:", obs.get("action_output"))

    finally:
        env.close()


if __name__ == "__main__":
    main()
