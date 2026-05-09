"""
OpenAI Computer Use Agent (CUA) Agent implementation.

This agent manages the interaction loop between the CUA operator and the desktop environment.
It handles conversation state, history management, action execution, and termination logic.
This is adopted from Co-Act.
"""
import base64
import json
import logging
import os
from typing import Any, Dict, List

from agentic_machines.core import BaseOperator, StepResult
from agentic_machines.agents.cua_operator import CUAOperator
from agentvm.desktop_env import DesktopEnv


logger = logging.getLogger("desktopenv.cua_agent")

PROMPT_TEMPLATE = """# Task
{task}

# Hints
- Sudo password is "{CLIENT_PASSWORD}".
- Keep the windows/applications opened at the end of the task.
- Do not use shortcut to reload the application except for the browser, just close and reopen.
- If you have completed the user task, reply with the information you want the user to know along with 'TERMINATE'.
- If you don't know how to continue the task, reply your concern or question along with 'IDK'.
""".strip()

DEFAULT_REPLY = "Please continue the user task. If you have completed the user task, reply with the information you want the user to know along with 'TERMINATE'."


def _cua_to_pyautogui(action) -> str:
    """Convert an Action (dict **or** Pydantic model) into a pyautogui call."""
    def fld(key: str, default: Any = None) -> Any:
        return action.get(key, default) if isinstance(action, dict) else getattr(action, key, default)

    act_type = fld("type")
    if not isinstance(act_type, str):
        act_type = str(act_type).split(".")[-1]
    act_type = act_type.lower()

    if act_type in ["click", "double_click"]:
        button = fld('button', 'left')
        if button == 1 or button == 'left':
            button = 'left'
        elif button == 2 or button == 'middle':
            button = 'middle'
        elif button == 3 or button == 'right':
            button = 'right'

        if act_type == "click":
            return f"pyautogui.click({fld('x')}, {fld('y')}, button='{button}')"
        if act_type == "double_click":
            return f"pyautogui.doubleClick({fld('x')}, {fld('y')}, button='{button}')"
        
    if act_type == "scroll":
        cmd = ""
        if fld('scroll_y', 0) != 0:
            cmd += f"pyautogui.scroll({-fld('scroll_y', 0) / 100}, x={fld('x', 0)}, y={fld('y', 0)});"
        return cmd
    
    if act_type == "drag":
        path = fld('path', [{"x": 0, "y": 0}, {"x": 0, "y": 0}])
        cmd = f"pyautogui.moveTo({path[0]['x']}, {path[0]['y']}, _pause=False); "
        cmd += f"pyautogui.dragTo({path[1]['x']}, {path[1]['y']}, duration=0.5, button='left')"
        return cmd

    if act_type == 'move':
        return f"pyautogui.moveTo({fld('x')}, {fld('y')})"

    if act_type == "keypress":
        keys = fld("keys", []) or [fld("key")]
        if len(keys) == 1:
            return f"pyautogui.press('{keys[0].lower()}')"
        else:
            return "pyautogui.hotkey('{}')".format("', '".join(keys)).lower()
        
    if act_type == "type":
        text = str(fld("text", ""))
        return "pyautogui.typewrite({:})".format(repr(text))
    
    if act_type == "wait":
        return "WAIT"
    
    return "WAIT"  # fallback


class CUAAgent(BaseOperator):
    """OpenAI Computer Use Agent that manages the interaction loop.
    
    This agent coordinates between the CUA operator (API calls) and the desktop
    environment, managing conversation history, action execution, and task completion.
    """
    
    def __init__(
        self,
        max_step: int,
        screen_width: int = 1920,
        screen_height: int = 1080,
        environment: str = "linux",
        sleep_after_execution: float = 0.3,
        truncate_history_inputs: int = 100,
        save_path: str = './',
        client_password: str = "",
        env: DesktopEnv = None,
    ):
        """Initialize the CUA agent.
        
        Args:
            max_step: Maximum number of steps to execute
            screen_width: Screen width for display
            screen_height: Screen height for display
            environment: Operating system environment
            sleep_after_execution: Sleep time after each action execution
            truncate_history_inputs: Maximum history length before truncation
            save_path: Path to save screenshots
            client_password: Client password for sudo commands
        """
        super().__init__()
        self.operator = CUAOperator(
            screen_width=screen_width,
            screen_height=screen_height,
            environment=environment
        )
        self.max_step = max_step
        self.sleep_after_execution = sleep_after_execution
        self.truncate_history_inputs = truncate_history_inputs
        self.save_path = save_path
        self.save_count = 0
        self.client_password = client_password
        self.env = env
    
    @classmethod
    def schema(cls) -> Dict[str, Any]:
        """Return the schema for the CUA agent."""
        return {
            "type": "function",
            "function": {
                "name": "cua_agent",
                "description": "Run OpenAI Computer Use Agent with interaction loop",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task": {
                                "type": "string",
                                "description": "The task instruction for the agent."
                            }
                        },
                    "required": ["task"],
                    "additionalProperties": False
                }
            }
        }
    
    def __call__(
        self,
        task: str,
    ) -> StepResult:
        """Execute the CUA agent (alias for run method).
        
        This allows the agent to be called as an operator.
        """
        return self.run(task)
    
    def _generate_task_summary(
        self,
        messages: List[Dict[str, Any]],
        reasoning_list: List[str],
        step_no: int,
        total_cost: float,
        tmp_path: str,
        hit_max_step: bool = False,
    ) -> tuple[str, float]:
        """Generate a comprehensive summary by calling the CUA agent.
        
        Args:
            messages: Conversation history
            reasoning_list: List of reasoning steps from the task execution
            step_no: Number of steps taken
            total_cost: Current total cost
            hit_max_step: Whether the task ended due to hitting max steps
            
        Returns:
            Tuple of (reasoning_text, updated_total_cost)
        """
        # Get the final screenshot
        obs = self.env.controller.get_screenshot()
        screenshot_b64 = base64.b64encode(obs).decode("utf-8")
        
        # Save the final screenshot
        with open(os.path.join(tmp_path, "final_screenshot.png"), "wb") as f:
            f.write(obs)
        
        # Build a list of past actions from reasoning_list
        past_actions_text = ""
        if reasoning_list:
            past_actions_text = "\n\nPast actions and reasoning:\n" + "\n".join([f"- {r}" for r in reasoning_list])
        
        # Different prompts based on completion status
        if hit_max_step:
            summary_prompt = f"""The task has reached the maximum step limit ({step_no} steps). Please analyze the current screenshot and provide a summary:

1. Describe what is visible in the current screenshot
2. What has been accomplished so far {past_actions_text}
3. What tasks remain incomplete or unfinished
4. What would be the next steps to complete the task

Please provide a clear summary of the current state."""
        else:
            summary_prompt = f"""1. Please analyze the current screenshot and describe what you see, describe what is visible in the current screenshot.
2. What has been accomplished so far {past_actions_text}
"""
        
        # Create a fresh message list for summary to avoid "Computer output cannot be provided together with more than one image input" error
        # Only include the first message (task context) and the new screenshot
        summary_messages = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": summary_prompt},
                {"type": "input_image", "image_url": f"data:image/png;base64,{screenshot_b64}"},
            ],
        }]
        
        # Call the operator to get the summary
        logger.info("Requesting summary from CUA agent...")
        summary_result = self.operator(messages=summary_messages)
        total_cost += summary_result.cost
        logger.info(f"Summary Cost: ${summary_result.cost:.6f} | Total Cost: ${total_cost:.6f}")
        
        # Extract the reasoning text from the response
        reasoning_parts = []
        for output_item in summary_result.raw_response.output:
            typ = output_item["type"] if isinstance(output_item, dict) else getattr(output_item, "type", None)
            if not isinstance(typ, str):
                typ = str(typ).split(".")[-1]
            
            if typ == "message":
                content = output_item.get("content", []) if isinstance(output_item, dict) else getattr(output_item, "content", [])
                if content and len(content) > 0:
                    text = content[0].get("text", "") if isinstance(content[0], dict) else getattr(content[0], "text", "")
                    reasoning_parts.append(text)
            elif typ == "reasoning":
                summary_text = output_item.get("summary", []) if isinstance(output_item, dict) else getattr(output_item, "summary", [])
                if summary_text and len(summary_text) > 0:
                    text = summary_text[0].get("text", "") if isinstance(summary_text[0], dict) else getattr(summary_text[0], "text", "")
                    reasoning_parts.append(text)
        
        # Format reasoning based on completion status
        if hit_max_step:
            reasoning = "Max steps reached. " + " ".join(reasoning_parts)
        else:
            reasoning = " ".join(reasoning_parts)
        
        return reasoning, total_cost
    
    def run(
        self,
        task: str,
    ) -> StepResult:
        """Run the CUA agent on a task.
        
        Args:
            env: Desktop environment instance
            task: Task instruction
            
        Returns:
            StepResult with execution results
        """
        # Initialize with first screenshot
        logger.info(f"Task: {task}")
        obs = self.env.controller.get_screenshot()
        screenshot_b64 = base64.b64encode(obs).decode("utf-8")
        
        # Create save path if it doesn't exist
        tmp_path = os.path.join(self.save_path, f"run_{self.save_count}")
        self.save_count += 1
        os.makedirs(tmp_path, exist_ok=True)
        
        with open(os.path.join(tmp_path, "initial_screenshot.png"), "wb") as f:
            f.write(obs)
            
        messages = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROMPT_TEMPLATE.format(
                    task=task, 
                    CLIENT_PASSWORD=self.client_password
                )},
                {"type": "input_image", "image_url": f"data:image/png;base64,{screenshot_b64}"},
            ],
        }]

        total_cost = 0.0
        step_no = 0
        
        reasoning_list = []
        reasoning = ""

        # Iterative dialogue
        while step_no < self.max_step:
            step_no += 1
            
            # Make API call at the start of each iteration
            step_result = self.operator(messages=messages)
            total_cost += step_result.cost
            logger.info(f"Cost: ${step_result.cost:.6f} | Total Cost: ${total_cost:.6f}")
            
            # Add cleaned history items from operator
            messages += step_result.input_items

            # Extract computer_call(s) and other items from response
            calls: List[Dict[str, Any]] = []
            breakflag = False
            raw_output = step_result.raw_response.output
            
            for i, o in enumerate(raw_output):
                typ = o["type"] if isinstance(o, dict) else getattr(o, "type", None)
                if not isinstance(typ, str):
                    typ = str(typ).split(".")[-1]
                    
                if typ == "computer_call":
                    calls.append(o if isinstance(o, dict) else o.model_dump())
                    print(f"\n[CUA Tool Call - Step {step_no}]:", flush=True)
                    print(f"  Action: {calls[-1].get('action', {})}", flush=True)
                elif typ == "reasoning" and len(o.summary) > 0:
                    reasoning = o.summary[0].text
                    reasoning_list.append(reasoning)
                    print(f"\n[CUA Thoughts - Step {step_no}]: {reasoning}", flush=True)
                    logger.info(f"[Reasoning]: {reasoning}")
                elif typ == 'message':
                    if 'TERMINATE' in o.content[0].text:
                        reasoning_list.append(f"Final output: {o.content[0].text}")
                        reasoning = "My thinking process\n" + "\n- ".join(reasoning_list) + '\nPlease check the screenshot and see if it fulfills your requirements.'
                        breakflag = True
                        break
                    if 'IDK' in o.content[0].text:
                        reasoning = f"{o.content[0].text}. I don't know how to complete the task. Please check the current screenshot."
                        breakflag = True
                        break
                    try:
                        json.loads(o.content[0].text)
                        messages.pop(len(messages) - len(raw_output) + i)
                        step_no -= 1
                    except Exception as e:
                        logger.info(f"[Message]: {o.content[0].text}")
                        if '?' in o.content[0].text:
                            messages += [{
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": DEFAULT_REPLY},
                                ],
                            }]
                        elif "{" in o.content[0].text and "}" in o.content[0].text:
                            messages.pop(len(messages) - len(raw_output) + i)
                            step_no -= 1
                        else:
                            logger.info(f"[Message]: {o.content[0].text}")
                            messages.pop(len(messages) - len(raw_output) + i)
                            reasoning = o.content[0].text
                            reasoning_list.append(reasoning)
                            step_no -= 1

            if breakflag:
                break

            # Execute actions
            for action_call in calls:
                py_cmd = _cua_to_pyautogui(action_call["action"])

                # Execute in VM
                obs, *_ = self.env.step(py_cmd, self.sleep_after_execution)

                # Send screenshot back
                screenshot_b64 = base64.b64encode(obs["screenshot"]).decode("utf-8")
                with open(os.path.join(tmp_path, f"step_{step_no}.png"), "wb") as f:
                    f.write(obs["screenshot"])
                    
                messages += [{
                    "type": "computer_call_output",
                    "call_id": action_call["call_id"],
                    "output": {
                        "type": "computer_screenshot",
                        "image_url": f"data:image/png;base64,{screenshot_b64}",
                    },
                }]
                
                # Handle safety checks
                if "pending_safety_checks" in action_call and len(action_call.get("pending_safety_checks", [])) > 0:
                    messages[-1]['acknowledged_safety_checks'] = [
                        {
                            "id": psc["id"],
                            "code": psc["code"],
                            "message": "Please acknowledge this warning if you'd like to proceed."
                        }
                        for psc in action_call.get("pending_safety_checks", [])
                    ]
            
            # Truncate history while preserving call_id pairs
            if len(messages) > self.truncate_history_inputs:
                original_history = messages[:]
                messages = [messages[0]] + messages[-self.truncate_history_inputs:]
                
                # Find all call_ids in the truncated history
                call_ids_in_truncated = set()
                for item in messages:
                    if isinstance(item, dict) and 'call_id' in item:
                        call_ids_in_truncated.add(item['call_id'])
                
                # Check if any call_ids are missing their pairs
                call_id_types = {}
                for item in messages:
                    if isinstance(item, dict) and 'call_id' in item:
                        call_id = item['call_id']
                        item_type = item.get('type', '')
                        if call_id not in call_id_types:
                            call_id_types[call_id] = []
                        call_id_types[call_id].append(item_type)
                
                # Find unpaired call_ids
                unpaired_call_ids = []
                for call_id, types in call_id_types.items():
                    has_call = 'computer_call' in types
                    has_output = 'computer_call_output' in types
                    if not (has_call and has_output):
                        unpaired_call_ids.append(call_id)
                
                # Add missing pairs from original history
                if unpaired_call_ids:
                    missing_items = []
                    for item in original_history:
                        if (isinstance(item, dict) and 
                            item.get('call_id') in unpaired_call_ids and 
                            item not in messages):
                            missing_items.append(item)
                    
                    # Insert missing items back
                    for missing_item in missing_items:
                        original_index = original_history.index(missing_item)
                        insert_pos = len(messages)
                        for i, existing_item in enumerate(messages[1:], 1):
                            if existing_item in original_history:
                                existing_original_index = original_history.index(existing_item)
                                if existing_original_index > original_index:
                                    insert_pos = i
                                    break
                        messages.insert(insert_pos, missing_item)
        
        logger.info(f"Total cost for the task: ${total_cost:.4f}")
        
        # Always generate summary by calling the CUA agent
        # Pass hit_max_step flag to customize the prompt
        hit_max_step = step_no >= self.max_step
        summary, total_cost = self._generate_task_summary(
            messages, reasoning_list, step_no, total_cost, tmp_path, hit_max_step=hit_max_step
        )
        logger.info(f"Total cost for the task (including summary): ${total_cost:.4f}")
        
        # Clean up image URLs in history for storage
        if messages and 'content' in messages[0]:
            for content_item in messages[0]['content']:
                if isinstance(content_item, dict) and content_item.get('type') == 'input_image':
                    content_item['image_url'] = "<image>"
        
        for item in messages:
            if item.get('type') == 'computer_call_output':
                if 'output' in item and 'image_url' in item['output']:
                    item['output']['image_url'] = "<image>"

        # Return StepResult
        return StepResult(
            kind="agent",
            name=self.name,
            finish_reason="success" if step_no < self.max_step else "max_step_limit",
            messages=messages,  
            output=summary,
            reasoning_list=reasoning_list,
            total_cost=total_cost,
            steps_taken=step_no
        )
