import json
import inspect
import asyncio
import base64
from typing import Optional, Dict, Any, Callable, List
from agentic_machines.core.base_operator import BaseOperator
from agentic_machines.core.step_result import StepResult
from agentic_machines.utils.llm_utils import LLMCaller
from agentvm.desktop_env import DesktopEnv
import os


TERMINATE = {
    "type": "function",
    "function": {
        "name": "terminate",
        "description": "This marks the end of the task and it terminates the process. If the task is done/failed, call this tool to terminate the process and submit the final answer. Don't terminate the task until all required deliverables are created.",
        "parameters": {
            "type": "object",
            "properties": {
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
            "required": ["answer", "finish_reason", "result_references"],
            "additionalProperties": False
        },
        "strict": True
    }
}

TAKE_NOTE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "take_note",
        "description": "Record a short note for the run log. This does not terminate the process.",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "The note to record."
                }
            },
            "required": ["note"],
            "additionalProperties": False
        },
        "strict": True
    }
}


def update_model_usage(usage_dict: dict, model: str, usage: dict, cost: float):
    """Update the model usage statistics."""
    if model not in usage_dict:
        usage_dict[model] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost": 0
        }
    usage_dict[model]["prompt_tokens"] += usage.get("prompt_tokens", 0)
    usage_dict[model]["completion_tokens"] += usage.get("completion_tokens", 0)
    usage_dict[model]["total_tokens"] += usage.get("total_tokens", 0)
    usage_dict[model]["cost"] += cost
    usage_dict["total_cost"] = usage_dict.get("total_cost", 0) + cost


class MMAgent(BaseOperator):
    def __init__(
        self,
        name: str,
        description: str,
        action_schemas: List[Dict[str, Any]],  # list of action schemas (tools)
        system_msg: str,
        llm_config: dict,
        allowed_action_dict: Optional[Dict[str, Any]] = None,  # mapping from action name to function/operator
        summarize_llm_config: Optional[dict] = None,
        max_step: int = 15,
        message_pruning_function: Optional[Callable[[List[dict]], tuple[bool, List[dict]]]] = None,
        reset_before_call: bool = False,
        max_images: int = 3,
        gui_tool_schemas: Optional[List[Dict[str, Any]]] = None,
    ):
        super().__init__()
        
        # Tool schema for calling this agent (agents are callable tools with a single string task).
        self._schema = {
            "type": "function",
            "function": {
                "name": str(name),
                "description": str(description),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "The task to be performed.",
                        }
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }
        self.save_id = 0
        
        # LLM configurations
        self.llm_config = llm_config
        self.summarize_llm_config = summarize_llm_config if summarize_llm_config else None
        self.reset_before_call = reset_before_call
        
        # Agent parameters
        self.system_msg = system_msg
        self.max_step = max_step
        self.message_pruning_function = message_pruning_function
        self.max_images = max_images
        self.screenshot_count = 0

        # Optional GUI tool schemas (list of dicts). If provided, only append screenshots
        # to messages when a GUI tool is called.
        self.gui_tool_names = self._extract_tool_names(gui_tool_schemas)
        
        # Action schemas - include terminate + take_note tools
        self.action_schemas = [TERMINATE, TAKE_NOTE_TOOL_SCHEMA] + action_schemas
        
        # Build action dictionary from schemas
        self.allowed_action_dict: Dict[str, Any] = allowed_action_dict
        
        # Initialize state
        self.messages = [{"role": "system", "content": self.system_msg}]
        self.usage = {"total_cost": 0}
        self.step_results = []
        self.save_folder = None
        self.log_file = None
    
    def reset(self, save_folder: str):
        """Reset the agent to its initial state."""
        self.messages = [{"role": "system", "content": self.system_msg}]
        self.usage = {"total_cost": 0}
        self.step_results = []
        self.save_folder = save_folder
        self.save_id += 1
        self.screenshot_count = 0
        self.log_file = os.path.join(save_folder, f"agent_{self.name}_prints_{self.save_id}.log")
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("")

    def _log_print(self, *args, **kwargs):
        """Print to stdout and append the same text to the agent log file."""
        sep = kwargs.get("sep", " ")
        end = kwargs.get("end", "\n")
        message = sep.join(str(arg) for arg in args) + end
        print(*args, **kwargs)
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(message)
            except Exception:
                pass

    def _print_agent_response(self, response_dict: dict, iteration: int):
        """Print a beautified agent response showing content and function calls."""
        self._log_print("\n" + "="*80, flush=True)
        self._log_print(f"🤖 AGENT RESPONSE (Iteration {iteration}/{self.max_step})", flush=True)
        self._log_print("="*80, flush=True)
        
        if response_dict.get('content'):
            self._log_print(f"💬 Content: {response_dict['content']}", flush=True)
        
        if response_dict.get('tool_calls'):
            self._log_print("\n🔧 Function Calls:", flush=True)
            for idx, tc in enumerate(response_dict.get('tool_calls', []), 1):
                func_name = tc['function'].get('name', 'unknown')
                args = tc['function'].get('arguments', '{}')
                try:
                    args_dict = json.loads(args)
                    args_str = ""
                    for k, v in args_dict.items():
                        args_str += f"{k}: {v}, "
                except Exception:
                    args_str = args
                self._log_print(f"\n  [{idx}] Function: {func_name}", flush=True)
                self._log_print(f"      Arguments: {args_str}", flush=True)
        
        self._log_print("="*80 + "\n", flush=True)

    def _print_tool_returns(self, tool_returns: List[dict]):
        """Print beautified tool return results."""
        self._log_print("\n" + "="*80, flush=True)
        self._log_print(f"📥 TOOL RETURNS ({len(tool_returns)} tool{'s' if len(tool_returns) != 1 else ''})", flush=True)
        self._log_print("="*80, flush=True)
        
        for idx, tr in enumerate(tool_returns, 1):
            tool_call_id = tr.get('tool_call_id', 'unknown')
            content = tr.get('content', '')
            
            # Try to format JSON content if possible
            try:
                if content and content.strip().startswith('{'):
                    content_dict = json.loads(content)
                    content_display = json.dumps(content_dict, indent=2)
                else:
                    content_display = content
            except Exception:
                content_display = content
            
            self._log_print(f"\n  [{idx}] Tool Call ID: {tool_call_id}", flush=True)
            self._log_print(f"      Content Length: {len(content)} characters", flush=True)

            # Show full content with truncation warning very long
            if len(content_display) > 4000:
                self._log_print(f"      Content (first 4000 chars):\n{content_display[:4000]}", flush=True)
                self._log_print(f"      ... (truncated {len(content_display) - 4000} characters)", flush=True)
            else:
                self._log_print(f"      Content:\n{content_display}", flush=True)
        
        self._log_print("="*80 + "\n", flush=True)
    
    def _add_msg(self, role: str, content: str):
        """Add a message to the conversation history."""
        self.messages.append({
            "role": role,
            "content": content,
        })
    
    def _prune_images_from_messages(self):
        """Prune old images from messages, keeping only the most recent max_images screenshots."""
        image_indices = []
        
        # Find all messages with images
        for idx, msg in enumerate(self.messages):
            if isinstance(msg.get("content"), list):
                for content_item in msg["content"]:
                    if isinstance(content_item, dict) and content_item.get("type") in ["image_url", "input_image"]:
                        image_indices.append(idx)
                        break
        
        # If we have more images than max_images, remove the oldest ones
        if len(image_indices) > self.max_images:
            indices_to_prune = image_indices[:-self.max_images]
            
            # Remove messages in reverse order to maintain correct indices
            for idx in reversed(indices_to_prune):
                del self.messages[idx]
    
    def _message_pruning(self):
        """Apply message pruning if a pruning function is provided."""
        if self.message_pruning_function is not None:
            is_pruned, pruned_messages = self.message_pruning_function(self.messages)
            if is_pruned:
                self.messages = pruned_messages
                return True
        return False

    def _extract_tool_names(self, schemas: Optional[List[Dict[str, Any]]]) -> Optional[set]:
        if not schemas:
            return None
        names: set = set()
        for schema in schemas:
            if not isinstance(schema, dict):
                continue
            func_def = schema.get("function", {})
            name = func_def.get("name")
            if name:
                names.add(str(name))
        return names if names else None

    def _should_attach_screenshot(self, response_dict: dict) -> bool:
        """Return True if a GUI tool was called and screenshot should be appended.

        If no GUI tool list is provided, default to True to preserve prior behavior.
        """
        if self.gui_tool_names is None:
            return True
        tool_calls = response_dict.get("tool_calls", []) or []
        for tc in tool_calls:
            func_name = (tc.get("function") or {}).get("name")
            if func_name in self.gui_tool_names:
                return True
        return False
    
    def _get_main_response(self) -> dict:
        """Get response from LLM with tools."""
        llm_config = self.llm_config.copy()
        llm_config['tools'] = self.action_schemas
        response = LLMCaller.call_llm_with_msgs(self.messages, llm_config)
        # print(f"in _get_main_response   {response} ")
        update_model_usage(self.usage, response.model, response.usage.model_dump(), response.cost)
        return response.choices[0].message.model_dump()
    
    def _collect_submit_answer(self, response_dict: dict) -> StepResult:
        """Collect and process the terminate tool call."""
        tool_calls = response_dict.get("tool_calls") or []
        terminate_call = next(
            (tc for tc in tool_calls if (tc.get("function") or {}).get("name") == "terminate"),
            None,
        )
        if terminate_call is None:
            return None

        arguments = (terminate_call.get("function") or {}).get("arguments", "{}")
        arguments = json.loads(arguments)
        
        # Parse the answer
        output = arguments.get("answer", "")
        if output is None:
            output = ""
        
        result = StepResult(
            kind="agent",
            name=self.name,
            output=output,
            result_references=arguments.get("result_references", "N/A"),
            finish_reason=arguments.get("finish_reason", "success"),
            interactions = self.process_messages(self.messages),
            info={
                "operation_summary": "Task completed via terminate",
                "llm_usage": self.usage,
                "step_results": [x.model_dump() if isinstance(x, StepResult) else x for x in self.step_results]
            }
        )
        return result


    def _execute_tool(self, message: dict):
        """Execute tools from the assistant message."""
        tool_returns = []
        for tool_call in message.get("tool_calls", []):
            function_call = tool_call.get("function", {})
            tool_call_id = tool_call.get("id", None)
            
            func_name = function_call.get("name", None)

            # Special-case: take_note is handled locally and always succeeds.
            if func_name == "take_note":
                tool_returns.append({
                    "tool_call_id": tool_call_id,
                    "role": "tool",
                    "content": "Note Recorded",
                })
                continue

            func = self.allowed_action_dict.get(func_name, None) if self.allowed_action_dict else None
            
            if func is None:
                tool_returns.append({
                    "tool_call_id": tool_call_id,
                    "role": "tool",
                    "content": f"Error: Tool '{func_name}' not found."
                })
                continue
            
            # Handle async functions/operators with async __call__ method
            if inspect.iscoroutinefunction(getattr(func, '__call__', None)):
                try:
                    # get the running loop if it was already created
                    loop = asyncio.get_running_loop()
                    close_loop = False
                except RuntimeError:
                    # create a loop if there is no running loop
                    loop = asyncio.new_event_loop()
                    close_loop = True
                
                is_success, func_return = loop.run_until_complete(self._a_execute_function(function_call))
                if close_loop:
                    loop.close()
            else:
                _, func_return = self._execute_function(function_call)
            
            # Handle StepResult
            if isinstance(func_return, StepResult):
                self.step_results.append(func_return)
                content = f"""{func_return.output}
"""
                if content is None:
                    content = getattr(func_return, "output", None)
                if content is None:
                    content = str(func_return.model_dump())
            else:
                content = str(func_return if func_return else "")
            
            
            tool_call_response = {
                "tool_call_id": tool_call_id,
                "role": "tool",
                "content": content,
            }
            tool_returns.append(tool_call_response)
        
        return tool_returns
    
    async def _a_execute_function(self, func_call) -> tuple[bool, Any]:
        """Execute an async function call and return the result.

        Args:
            func_call: Dictionary with keys "name" and "arguments".

        Returns:
            Tuple of (is_exec_success, result).
            is_exec_success: Whether execution was successful.
            result: The function's return value or error message.
        """
        func_name = func_call.get("name", "")
        func = self.allowed_action_dict.get(func_name, None)
        
        if func is None:
            return False, f"Error: Tool '{func_name}' not found."
            
        try:
            arguments = json.loads(func_call.get("arguments", "{}"))
        except json.JSONDecodeError as e:
            return False, f"Error parsing arguments: {str(e)}"
            
        try:
            # Check if it's an async operator with __call__ method
            if inspect.iscoroutinefunction(getattr(func, '__call__', None)):
                result = await func(**arguments)
            else:
                result = func(**arguments)
            return True, result
        except Exception as e:
            return False, f"Error executing {func_name}: {str(e)}"

    def quick_bash_script(self, script: str) -> str:
        """Quickly run a bash script in the environment and return output."""
        action_dict = {
            "name": "run_bash_script",
            "arguments": json.dumps({
                "script": script
            })
        }
        is_success, result = self._execute_function(action_dict)
        self._log_print(f"Quick bash script executed. Success: {is_success}")
        self._log_print(f"Result: {result}")
            
    def _execute_function(self, func_call) -> tuple[bool, Any]:
        """Execute a function call and return the result.

        Args:
            func_call: Dictionary with keys "name" and "arguments".
        Returns:
            Tuple of (is_exec_success, result).
            is_exec_success: Whether execution was successful.
            result: The function's return value or error message.
        """
        func_name = func_call.get("name", "")
        func = self.allowed_action_dict.get(func_name, None)
        
        if func is None:
            return False, f"Error: Tool '{func_name}' not found."
            
        try:
            arguments = json.loads(func_call.get("arguments", "{}"))
        except json.JSONDecodeError as e:
            return False, f"Error parsing arguments: {str(e)}"
            
        try:
            result = func(**arguments)
            return True, result
        except Exception as e:
            return False, f"Error executing {func_name}: {str(e)}"
    
    def _save_run(self, task, step_result: StepResult, save_folder: str):
        """Save the agent run details to a JSON file."""
        
        save_data = {
            "task": task,
            "step_result": step_result.model_dump() if isinstance(step_result, StepResult) else step_result,
        }
        
        save_file = f"{save_folder}/agent_{self.name}_run_{self.save_id}.json"
        with open(save_file, 'w') as f:
            json.dump(save_data, f, indent=2)
        
        # print_agent_save_path(save_file)
    
    
    def process_messages(self, messages: List[dict]) -> List[dict]:
        """Process messages for storage/display.
        
        Always removes image data from messages to reduce storage size.
        
        Args:
            messages: List of message dictionaries
        
        Returns:
            Processed messages with images removed
        """
        processed_messages = []
        for msg in messages:
            new_msg = {
                "role": msg["role"],
                "content": msg["content"]
            }
            
            # Always remove images for storage
            if isinstance(new_msg["content"], list):
                cleaned_content = []
                for content_item in new_msg["content"]:
                    if isinstance(content_item, dict):
                        if content_item.get("type") not in ["image_url", "input_image"]:
                            cleaned_content.append(content_item)
                        else:
                            # Replace image with placeholder
                            cleaned_content.append({
                                "type": "text",
                                "text": "[Screenshot removed]"
                            })
                    else:
                        cleaned_content.append(content_item)
                new_msg["content"] = cleaned_content if cleaned_content else "[Screenshot removed]"

            # process tool_calls
            if "tool_calls" in msg and isinstance(msg["tool_calls"], list):
                for i, tool_call in enumerate(msg["tool_calls"]):
                    func = tool_call.get("function", {})
                    name = func.get("name", "")
                    arguments = func.get("arguments", "")
                    new_msg[f"function_{i}"] = f"NAME: {name}\nARGUMENTS: {arguments}"
            processed_messages.append(new_msg)
        return processed_messages

    def __call__(self, task: str, env: DesktopEnv, save_folder: str) -> StepResult:
        """        
        Args:
            task (str): The task or problem description to be solved by the agent.            
            env (DesktopEnv): The desktop environment instance for interaction and recording.
            save_folder (str): The folder path to save agent run data and recordings.
        Returns:
            StepResult: The result of the agent execution
        """
        assert save_folder is not None, "Save folder must be provided."
        if self.reset_before_call:
            self.reset(save_folder=save_folder)
        else:
            self.save_folder = save_folder
        
        os.makedirs(save_folder, exist_ok=True)
        os.makedirs(f"{save_folder}/Recordings", exist_ok=True)
        os.makedirs(f"{save_folder}/Screenshots", exist_ok=True)
        self.log_file = os.path.join(save_folder, f"agent_{self.name}_prints_{self.save_id}.log")
        with open(self.log_file, "w", encoding="utf-8") as f:
            f.write("")
        
        # Get initial screenshot
        obs = env.controller.get_screenshot()
        screenshot_b64 = base64.b64encode(obs).decode("utf-8")
        
        # Save initial screenshot
        screenshot_path = os.path.join(save_folder, "Screenshots", f"screenshot_{self.screenshot_count:03d}_initial.png")
        with open(screenshot_path, "wb") as f:
            f.write(obs)
        self.screenshot_count += 1
        
        # Add initial message with task and screenshot
        self.messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": task.strip()},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
            ],
        })
        
        result = None
        # Main agent loop
        for i in range(self.max_step):
            # ==========================================            
            # ----------- Get Agent Response -----------
            # Get response from LLM
            response_dict = self._get_main_response()
            self.messages.append(response_dict)
            
            # Print agent response
            self._print_agent_response(response_dict, i + 1)

            # ----------------------------------------
            # ----------- Get Tool Results -----------
            # EXIT POINT
                # Check for terminate
            if response_dict.get("tool_calls") and \
                    any(tc['function'].get("name") == "terminate" 
                   for tc in response_dict.get("tool_calls", [])):
                result = self._collect_submit_answer(response_dict)
                env.controller.get_folder(folder_path="/home/user/Downloads/", dest_path=f"{save_folder}/Downloads")
                if result:
                    break
            
            # Execute tools if present
            if response_dict.get("tool_calls"):
                env.controller.start_recording()

                tool_returns = self._execute_tool(response_dict)
                self._print_tool_returns(tool_returns)
                self.messages.extend(tool_returns)

                env.controller.get_folder(folder_path="/home/user/Downloads/", dest_path=f"{save_folder}/Downloads")
                env.controller.end_recording(dest=f"{save_folder}/Recordings/agent_{self.name}_step_{i}.mp4")
            else:
                # No tool calls, just continue
                self._add_msg(
                    role="user",
                    content="Continue. When finished, carefully read the task requirement and submit the final answer with correct format."
                )

            # ----------------------------------------
            # ----------- End of Step Work -----------
            # Get screenshot after step completion
            if self._should_attach_screenshot(response_dict):
                obs = env.controller.get_screenshot()
                screenshot_b64 = base64.b64encode(obs).decode("utf-8")
                
                # Print window state for debugging
                window_state = env.get_window_state()
                # self._log_print(f"---- Debug Info: Window State after step {i+1} ----", flush=True)
                # self._log_print(window_state, flush=True)
                
                # Save screenshot
                screenshot_path = os.path.join(save_folder, "Screenshots", f"screenshot_{self.screenshot_count:03d}_step_{i+1}.png")
                with open(screenshot_path, "wb") as f:
                    f.write(obs)
                self.screenshot_count += 1
                
                # Add screenshot to messages
                self.messages.append({
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Window state after step {i+1}: {window_state}. Attached is the screenshot."},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                    ],
                })
            
            # Apply message pruning if configured
            self._message_pruning()
            
            # Prune old images to keep only recent ones
            self._prune_images_from_messages()
            
            # checkpoint
            result = StepResult(
                kind="agent",
                name=self.name,
                output="N/A",
                finish_reason="not_finished",
                result_references="N/A",
                interactions = self.process_messages(self.messages),
                info={
                    "operation_summary": "Maximum iterations reached without completion",
                    "llm_usage": self.usage,
                    "step_results": [x.model_dump() if isinstance(x, StepResult) else x for x in self.step_results]
                }
            )
            self._save_run(task, result, save_folder=save_folder)
            # ==========================================
        
        # Max iterations reached
        if result is None:
            output = "N/A"
            if self.summarize_llm_config:
                # Attempt to summarize
                try:
                    summary_prompt = "Please summarize what has been accomplished so far."
                    self._add_msg("user", summary_prompt)
                    response_dict = self._get_main_response()
                    output = response_dict.get("content", "N/A")
                    self._log_print(response_dict, flush=True)
                except Exception:
                    self._log_print(
                        f"Error during summarization with model {self.summarize_llm_config.get('model', 'unknown')}",
                        flush=True,
                    )
                    output = "Max iterations reached, could not summarize."
            
            result = StepResult(
                kind="agent",
                name=self.name,
                output=output,
                finish_reason="max_step_limit",
                result_references="N/A",
                interactions = self.process_messages(self.messages),
                info={
                    "operation_summary": "Maximum iterations reached without completion",
                    "llm_usage": self.usage,
                    "step_results": [x.model_dump() if isinstance(x, StepResult) else x for x in self.step_results]
                }
            )
        self._save_run(task, result, save_folder=save_folder)
        return result

    def run(self, task: str, env: DesktopEnv, save_folder: str) -> StepResult:
        """Alias for __call__ method with proper parameter passing.
        
        Args:
            task: The task description
            env: Desktop environment instance
            save_folder: Folder to save results
            
        Returns:
            StepResult from agent execution
        """
        return self.__call__(task=task, env=env, save_folder=save_folder)