"""Script to run end-to-end evaluation on the benchmark.
Utils and basic architecture credit to https://github.com/web-arena-x/webarena/blob/main/run.py.
"""

import textwrap
import traceback
import argparse
import datetime
import json
import logging
import os
import sys
import yaml

from s2_5.agents.agent_s import AgentS2_5 as AgentS2
from s2_5.agents.grounding import OSWorldACI
from tqdm import tqdm
from agentvm.desktop_env import DesktopEnv

import time
from wrapt_timeout_decorator import *
import dotenv

dotenv.load_dotenv()

logger = logging.getLogger("desktopenv.experiment")
#  Logger Configs {{{ #
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

datetime_str: str = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

file_handler = logging.FileHandler(
    os.path.join("logs", "normal-{:}.log".format(datetime_str)), encoding="utf-8"
)
debug_handler = logging.FileHandler(
    os.path.join("logs", "debug-{:}.log".format(datetime_str)), encoding="utf-8"
)
stdout_handler = logging.StreamHandler(sys.stdout)
sdebug_handler = logging.FileHandler(
    os.path.join("logs", "sdebug-{:}.log".format(datetime_str)), encoding="utf-8"
)

file_handler.setLevel(logging.INFO)
debug_handler.setLevel(logging.DEBUG)
stdout_handler.setLevel(logging.INFO)
sdebug_handler.setLevel(logging.DEBUG)

formatter = logging.Formatter(
    fmt="\x1b[1;33m[%(asctime)s \x1b[31m%(levelname)s \x1b[32m%(module)s/%(lineno)d-%(processName)s\x1b[1;33m] \x1b[0m%(message)s"
)
file_handler.setFormatter(formatter)
debug_handler.setFormatter(formatter)
stdout_handler.setFormatter(formatter)
sdebug_handler.setFormatter(formatter)

stdout_handler.addFilter(logging.Filter("desktopenv"))
sdebug_handler.addFilter(logging.Filter("desktopenv"))

logger.addHandler(file_handler)
logger.addHandler(debug_handler)
logger.addHandler(stdout_handler)
logger.addHandler(sdebug_handler)
#  }}} Logger Configs #
def setup_logger(example, example_result_dir):
    runtime_logger = logging.getLogger(f"desktopenv.example.{example['id']}")
    runtime_logger.setLevel(logging.DEBUG)
    runtime_logger.addHandler(logging.FileHandler(os.path.join(example_result_dir, "runtime.log")))
    return runtime_logger



def config() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run case study"
    )

    # environment config
    parser.add_argument("--path_to_vm", type=str, default="/home/yiran/osworld/docker_vm_data/Ubuntu.actions.qcow2")
    parser.add_argument(
        "--headless", action="store_true", help="Run in headless machine"
    )
    parser.add_argument(
        "--observation_type",
        choices=["screenshot", "a11y_tree", "screenshot_a11y_tree", "som"],
        default="screenshot",
        help="Observation type",
    )
    parser.add_argument("--screen_width", type=int, default=1920)
    parser.add_argument("--screen_height", type=int, default=1080)
    parser.add_argument("--sleep_after_execution", type=float, default=3)
    parser.add_argument("--max_steps", type=int, default=15)

    # agent config
    parser.add_argument("--max_trajectory_length", type=int, default=15)
    parser.add_argument(
        "--test_config_base_dir", type=str, default="evaluation_examples"
    )

    # lm config
    parser.add_argument("--model_provider", type=str, default="openai")
    parser.add_argument("--model", type=str, default=os.environ.get("MODEL", "gpt-5.2"))
    parser.add_argument(
        "--model_url",
        type=str,
        default="",
        help="The URL of the main generation model API.",
    )
    parser.add_argument(
        "--model_api_key",
        type=str,
        default=os.environ.get("MODEL_API_KEY", ""),
        help="The API key of the main generation model.",
    )
    parser.add_argument("--temperature", type=float, default=float(os.environ.get("MODEL_TEMPERATURE", "1.0")))
    parser.add_argument("--top_p", type=float, default=0.9)
    parser.add_argument("--max_tokens", type=int, default=1500)
    parser.add_argument("--stop_token", type=str, default=None)

    # logging related
    parser.add_argument("--result_dir", type=str, default="./s2_results")
    parser.add_argument("--trial", type=str, default="0")
    
    # task config
    parser.add_argument("-t", "--task", type=str, required=True,
                        help="Comma-separated list of task yaml names (e.g., admin_task,lawyer_task)")

    # Configuration 1
    parser.add_argument("--grounding_model_provider", type=str, default=os.environ.get("GROUND_PROVIDER", "huggingface"))
    parser.add_argument(
        "--grounding_model", type=str, default=os.environ.get("GROUND_MODEL")
    )
    parser.add_argument(
        "--grounding_model_resize_width",
        type=int,
        default=1366,
        help="Width of screenshot image after processor rescaling",
    )
    parser.add_argument(
        "--grounding_model_resize_height",
        type=int,
        default=None,
        help="Height of screenshot image after processor rescaling",
    )

    # Configuration 2
    parser.add_argument("--endpoint_provider", type=str, default="")
    parser.add_argument("--endpoint_url", type=str, default=os.environ.get("GROUND_URL", ""))
    parser.add_argument(
        "--endpoint_api_key",
        type=str,
        default=os.environ.get("GROUND_API_KEY", ""),
        help="The API key of the grounding model.",
    )

    parser.add_argument("--kb_name", default="kb_s2", type=str)

    args = parser.parse_args()

    return args


def test(args: argparse.Namespace, tasks: list) -> None:
    # log args
    logger.info("Args: %s", args)

    # NEW!
    engine_params = {
        "engine_type": args.model_provider,
        "model": args.model,
        "base_url": args.model_url,
        "api_key": args.model_api_key,
        "temperature": args.temperature,
    }

    engine_params_for_grounding = {
        "engine_type": args.grounding_model_provider,
        "model": args.grounding_model,
        "base_url": args.endpoint_url,
        "api_key": args.endpoint_api_key,
        "grounding_width": args.grounding_model_resize_width,
        "grounding_height": args.grounding_model_resize_height,
    }

    # NEW!
    grounding_agent = OSWorldACI(
        platform="linux",
        engine_params_for_generation=engine_params,
        engine_params_for_grounding=engine_params_for_grounding,
        width=args.screen_width,
        height=args.screen_height,
        # custom_actions=
    )

    logger.info("Setting up environment...")
    
    # NEW!
    agent = AgentS2(
        engine_params,
        grounding_agent,
        platform="linux",
    )

    env = DesktopEnv(
        path_to_vm=args.path_to_vm,
        screen_size=(args.screen_width, args.screen_height),
        headless=args.headless,
        os_type="Ubuntu",
        provider_name="docker",
        require_a11y_tree=args.observation_type
        in ["a11y_tree", "screenshot_a11y_tree", "som"],
    )
    
    logger.info("Environment is ready. Starting sequential task execution...")

    # Run tasks sequentially
    for task_name, example in tqdm(tasks, desc="Tasks"):
        task_id = example['id']
        example['instruction'] += textwrap.dedent("""
            All reference files are placed under Downloads folder in the VM.
            All your deliverables should also be saved under Downloads folder.""")
        
        logger.info(f"[Task]: {task_name}")
        logger.info(f"[Instruction]: {example['instruction']}")
        if 'setup_config' not in example:   
            example['setup_config'] = []
        example['setup_config'].append({
            "type": "register_action",
            "parameters": {
            "actions": [
                {"local_path": "actions/execute_bash"},
                {"local_path": "actions/file_reader"},
                {"local_path": "actions/pandoc_converter"},
                {"local_path": "actions/run_python"},
                {"local_path": "actions/str_replace_editor"},
                {"local_path": "actions/text_web_browser"}
            ]
            }
        })
        
        example_result_dir = os.path.join(
            args.result_dir,
            args.observation_type,
            args.model,
            args.trial,
            task_name,
        )
        os.makedirs(example_result_dir, exist_ok=True)

        # Start timing
        start_time = time.time()
        
        try:
            run_single_example(
                agent,
                env,
                example,
                args.max_steps,
                example["instruction"],
                args.sleep_after_execution,
                example_result_dir,
            )

            # Save timing info to separate file
            with open(os.path.join(example_result_dir, "timing.json"), "w") as f:
                json.dump({
                    "task_name": task_name,
                    "task_id": task_id,
                    "duration_minutes": round((time.time() - start_time) / 60, 2)
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Exception in {task_name}/{task_id}: {e}")
            traceback.print_exc()
            env.controller.end_recording(
                os.path.join(example_result_dir, "recording.mp4")
            )
            env.controller.get_folder(folder_path="/home/user/Downloads/", dest_path=f"{example_result_dir}/Downloads")

            with open(os.path.join(example_result_dir, "error.txt"), "a") as f:
                f.write(str(e))
            # Save timing info to separate file even on error
            with open(os.path.join(example_result_dir, "timing.json"), "w") as f:
                json.dump({
                    "task_name": task_name,
                    "task_id": task_id,
                    "duration_minutes": round((time.time() - start_time) / 60, 2),
                    "status": "failed"
                }, f, indent=2)
    
    env.close()
    logger.info(f"All tasks completed.")



def run_single_example(agent, env: DesktopEnv, example, max_steps, instruction, sleep_after_execution, example_result_dir):
    # runtime_logger = setup_logger(example, example_result_dir)
    agent.reset()

    env.reset(task_config=example)
    print(f"🌐 VNC Web Access: http://localhost:{env.vnc_port}")
    time.sleep(5)
    obs = env._get_obs()	    

    action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
    with open(os.path.join(example_result_dir, f"step_reset_{action_timestamp}.png"), "wb") as _f:
        _f.write(obs['screenshot'])
    
    with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
        traj_json = {
            "step_num": 0,
            'instruction': instruction,
            "action_timestamp": action_timestamp,
            "action": "reset",
            "reward": 0,
            "done": False,
            "info": {},
            "screenshot_file": f"step_reset_{action_timestamp}.png"
        }
        f.write(json.dumps(traj_json))
        f.write("\n")
    
    done = False
    step_idx = 0
    env.controller.start_recording()
    while not done and step_idx < max_steps:
        response, actions, input_messages = agent.predict(
            instruction,
            obs,
        )
        for action in actions:
            # Capture the timestamp before executing the action
            action_timestamp = datetime.datetime.now().strftime("%Y%m%d@%H%M%S")
            logger.info("Step %d: %s", step_idx + 1, action)
            obs, reward, done, info = env.step(action, sleep_after_execution)

            logger.info("Reward: %.2f", reward)
            logger.info("Done: %s", done)
            # Save screenshot and trajectory information
            with open(os.path.join(example_result_dir, f"step_{step_idx + 1}_{action_timestamp}.png"),
                      "wb") as _f:
                _f.write(obs['screenshot'])

            with open(os.path.join(example_result_dir, "traj.jsonl"), "a") as f:
                traj_json = {
                    "step_num": step_idx + 1,
                    "prediction": response,
                    "action_timestamp": action_timestamp,
                    "action": action,
                    "reward": reward,
                    "done": done,
                    "info": info,
                    "screenshot_file": f"step_{step_idx + 1}_{action_timestamp}.png",
                    "messages": input_messages,
                }
                f.write(json.dumps(traj_json))
                f.write("\n")
            if done:
                logger.info("The episode is done.")
                break
        step_idx += 1   

    env.controller.end_recording(os.path.join(example_result_dir, "recording.mp4"))
    env.controller.get_folder(folder_path="/home/user/Downloads/", dest_path=f"{example_result_dir}/Downloads")



if __name__ == "__main__":
    ####### The complete version of the list of examples #######
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    args = config()

    # Load tasks from YAML files
    task_names = [t.strip() for t in args.task.split(',')]
    tasks = []
    
    for task_name in task_names:
        yaml_path = os.path.join(
            os.path.dirname(__file__),
            "tasks",
            "agent_s_yaml",
            f"{task_name}.yaml"
        )
        
        if not os.path.exists(yaml_path):
            logger.error(f"Task YAML file not found: {yaml_path}")
            continue
            
        logger.info(f"Loading task from: {yaml_path}")
        with open(yaml_path, 'r', encoding='utf-8') as f:
            task_config = yaml.safe_load(f)
            if 'task' in task_config:
                tasks.append((task_name, task_config['task']))
            else:
                logger.error(f"Invalid YAML structure in {yaml_path}: missing 'task' key")
    
    # Log task info
    logger.info(f"Loaded {len(tasks)} tasks: {', '.join([t[0] for t in tasks])}")

    test(args, tasks)
