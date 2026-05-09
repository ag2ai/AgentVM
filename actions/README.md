
# AgentVM Actions

In AG2, we define an **action** to be a collection of tools that are bundled together targeting a specific domain or functionality. For example, the `text_web_browser` action enables the agent to perform actions such as 'search', 'visit', 'page_up', etc.

We organize each action as an independent folder containing all the necessary files and scripts, so that any action is portable and can be easily used with an agent, or added to an environment.

## Understanding the Action Folder

The complete structure: 
```
<action-name>/
    ├── bin/                  # !!! Required, Executable scripts
    ├── lib/                  # Good to Have, Library files if needed
    ├── tests/                # Good to Have, Test scripts
    ├── schema.yaml           # !!! Required, defined tool schema for registration and execution
    ├── install.sh            # !!! Optional but frequently used, Installation dependencies
    ├── state.json            # Optional, current state of the tool
    ├── config.json           # Optional, config file, any passed in config will be used to update this.
    └── README.md             # Good to Have, Tool description and usage
```

### Key Components
While a complete action folder can contain many files, we highlight the following components:
1. `bin/` - This folder contains all the executable scripts that define the actions of the action.
2. `schema.yaml` - This file defines the tool schema for registration and execution.
3. `install.sh` - This is not required but commonly used. This script will be used to install any dependencies required for the action to function properly and an `.venv` will be created inside the action folder. This ensures that the action has its own isolated environment, preventing any conflicts with other actions or the main agent environment.
4. `config.json` - You should use this file to store any configuration parameters needed for the action and load it when your action is called. When creating an environment / action, we will allow passing in configuration parameters that will be used to update this file.

Below is an example action folder structure:

```
example_action/
    ├── bin/
    │   ├── action_one
    │   └── action_two
    ├── schema.yaml
    ├── config.json
    └── install.sh
```

The schema.yaml file defines the actions available in the action:

```yaml
actions:
  action_one:
    signature: |
        action_one arg1
    docstring: |
        The docstring for action one.
    arguments:
      - name: arg1
        type: <type>
        description: Description for arg1.
        required: <true/false>
  action_two:
    ...
```

For each file in `\bin/` (omit the `.py`), there should be a corresponding action defined in `schema.yaml` with the same name. The action definition includes the signature, docstring, and arguments required for the action.

### Optional Components

1. `lib/` - This folder can contain any additional library files that the action may require. You can organize any helper files here.
2. `tests/` - It is a good practice to include test scripts in this folder to ensure the action's functionality.
3. `state.json` - If your action is stateful, it is a good practice to maintain a `state.json` file to keep track of the current state of the action.
4. `README.md` - A README file is highly recommended to provide a description of the action, its purpose, and usage instructions.


## How to setup and use the actions

When creating an environment, you can specify any actions you want to include by providing the action folder paths. The environment will automatically load the actions and make them available to the agent.

```python
from agentvm.desktop_env import DesktopEnv

env = DesktopEnv(provider_name="docker", os_type="Ubuntu")

# Register bash action
setup_config = [
    {
        "type": "register_action",
        "parameters": {
            "actions": [
                {"local_path": "./actions/execute_bash"}
            ]
        }
    }
]

obs = env.reset(setup_config=setup_config)
```

For each action, you can pass in the following arguments when calling the action:
```python
    {
         "path" : "/path/to/your/action/folder",
         "actions": ["action_one", "action_two"],  # Optional, list of actions to enable from the action. Default to all actions defined in schema.yaml
         "config": {                      # Optional config to update action's config.json
             "param1": "value1",
             "param2": "value2"
         },
         "force_reinstall": bool,   # (optional) if True, reinstall even if already installed
    },
```

Then to execute an action from the action, you can call it like this:

```
print(env.get_action_space()) # Get the available actions from the environment (including action actions)

env.step({
    "name": "action_one",
    "arguments": {
        "arg1": "some_value"
    }
}) # Execute an action from the action
```


How the actions will be loaded:
1. The environment will read the `schema.yaml` file to register the actions defined in the action.
2. The passed folder will be copied into the docker / vm environment placed under `actions/` directory. (A temporary config.json will be created from native config.json + what is passed in)
3. If `install.sh` is present, it will be executed to install any dependencies and create a `.venv` inside this action folder.
4. This action is now ready to be used by the agent.


## Available Action Bundles

Located in `actions/` directory:

| Bundle | Description | Example Actions |
|--------|-------------|-----------------|
| `execute_bash` | Execute bash scripts | Run shell commands |
| `file_reader` | Open a file in text format | open_file, page_up, find, etc |
| `run_python` | Execute Python code | Run Python scripts |
| `str_replace_editor` | Edit files | Create, edit, view files |
| `text_web_browser` | Web browsing (need SERPAPI_KEY) | search, visit, page_up, page_down, etc |
| `pandoc_converter` | Document conversion | Convert between formats |
| `mcp_*` | Application-specific MCP tools | LibreOffice, Chrome, VLC |