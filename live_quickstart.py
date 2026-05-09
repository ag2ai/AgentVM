from agentvm.desktop_env import DesktopEnv
import argparse
import time

# set log level
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

setup_config = [
]

# Initialize DesktopEnv
env = DesktopEnv(
    provider_name="docker",
    path_to_vm="./docker_vm_data/Ubuntu.actions.qcow2",
    os_type="Ubuntu",
    headless=False,
)

print("Starting OSWorld environment...")
obs = env.reset(setup_config=setup_config)
print("Environment reset complete!")

# Print connection info
print("\n" + "="*70)
print("🎥 LIVE DESKTOP VIEW - Access via Web Browser")
print("="*70)

# Check for Docker container and get ports
try:
    import subprocess
    result = subprocess.run(['docker', 'ps', '--format', '{{.Names}}\t{{.Ports}}'], 
                          capture_output=True, text=True)
    print("\nDocker containers and ports:")
    print(result.stdout)
except:
    pass

if hasattr(env, 'vnc_port'):
    print(f"\n VNC port: {env.vnc_port}")
    print(f"Try: http://localhost:{env.vnc_port}")


try:
    while True:
        # Read command input (supports multi-line)
        print("Enter bash command (multi-line supported, empty line to execute, 'quit' to exit):")
        lines = []
        first_line = input("$ ").strip()
        
        if first_line.lower() == 'quit':
            print("Exiting...")
            break
        
        if not first_line:
            # Just keep the environment alive
            time.sleep(0.1)
            continue
        
        lines.append(first_line)
        
        # Read additional lines until empty line
        while True:
            line = input("  ")
            if not line.strip():  # Empty line signals end
                break
            lines.append(line)
        
        command = '\n'.join(lines)
        
        # Create bash action
        action_dict = {
            "action_type": "run_bash_script",
            "arguments": {
                "script": command
            }
        }
        
        try:
            obs, reward, done, info = env.step(action_dict)
            # Print the action output
            if 'action_output' in obs:
                for k, v in obs['action_output'].items():
                    print(f"{k}: {v}")
            if done:
                print("Task marked as done!")
        except Exception as e:
            print(f"✗ Error: {e}")

except KeyboardInterrupt:
    print("\n\nInterrupted by user (Ctrl+C)")

# Clean up
print("\nClosing environment...")
env.close()
print("Environment closed.")

# Help users stop the Docker instance when needed
print("\n🛑 To stop the Docker instance:")
print("  - Run 'docker ps' to find the CONTAINER ID or NAME")
print("  - Then run: docker stop <CONTAINER_ID_OR_NAME>")
