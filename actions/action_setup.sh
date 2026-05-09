#!/bin/bash
# Tool setup script - installs Python 3.11 and sets up per-bundle isolated environments
# Optional: accepts action names as arguments to only install specific actions
set -euo pipefail

ACTIONS_DIR="/home/user/actions"
PY311_DIR="$ACTIONS_DIR/python3.11"
SUDO_PASSWORD="password"

# Parse arguments: if provided, only install these actions; otherwise install all with install.sh
ACTIONS_TO_INSTALL=()
if [ $# -gt 0 ]; then
    ACTIONS_TO_INSTALL=("$@")
fi

echo "[action_setup] Using actions dir: $ACTIONS_DIR"
echo "[action_setup] Python 3.11 will be installed at: $PY311_DIR"
if [ ${#ACTIONS_TO_INSTALL[@]} -gt 0 ]; then
    echo "[action_setup] Will install specific actions: ${ACTIONS_TO_INSTALL[*]}"
fi

# Authenticate sudo once (credentials will be cached for subsequent commands)
echo "$SUDO_PASSWORD" | sudo -S true 2>/dev/null

# Step 1: Install Python 3.11 if not already present
if [ ! -x "$PY311_DIR/bin/python3" ]; then
    echo "[action_setup] Installing Python 3.11..."

    # Wait for apt lock to be released (up to 5 minutes)
    echo "[action_setup] Waiting for apt lock to be released..."
    timeout=300
    elapsed=0
    while sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1 || sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1; do
        if [ $elapsed -ge $timeout ]; then
            echo "[action_setup] Timeout waiting for apt lock. Killing blocking processes..."
            sudo killall -9 apt apt-get packagekitd unattended-upgrade 2>/dev/null || true
            sleep 2
            break
        fi
        echo "[action_setup] Waiting for package manager lock... ($elapsed/$timeout seconds)"
        sleep 5
        elapsed=$((elapsed + 5))
    done

    # Install Python 3.11 using deadsnakes PPA
    export DEBIAN_FRONTEND=noninteractive

    # Fix APT cache corruption issues
    echo "[action_setup] Cleaning APT cache..."
    sudo rm -rf /var/cache/apt/*.bin 2>/dev/null || true
    sudo mkdir -p /var/cache/apt/archives/partial
    sudo mkdir -p /var/lib/apt/lists/partial

    sudo -E apt-get update -qq
    sudo -E apt-get install -y -qq software-properties-common
    sudo -E add-apt-repository ppa:deadsnakes/ppa -y
    sudo -E apt-get update -qq
    sudo -E apt-get install -y -qq python3.11 python3.11-venv python3.11-dev
    sudo -E apt-get install -y -qq pandoc

    # Create custom installation directory and symlink Python 3.11 to it
    mkdir -p "$PY311_DIR/bin"
    ln -sf /usr/bin/python3.11 "$PY311_DIR/bin/python3"
    ln -sf /usr/bin/python3.11 "$PY311_DIR/bin/python"

    # IMPORTANT:
    # Do NOT bootstrap pip globally for Python 3.11 here.
    echo "[action_setup] Skipping global pip install for Python 3.11 (avoids /usr/local/bin/pip mismatch)."

    echo "[action_setup] Python 3.11 installed successfully at $PY311_DIR"
else
    echo "[action_setup] Python 3.11 already exists at $PY311_DIR"
fi

# Step 1.5: Create a root venv at the actions dir (one-time)
ROOT_VENV="$ACTIONS_DIR/.venv"
if [ ! -x "$ROOT_VENV/bin/python" ]; then
    echo "[action_setup] Creating root venv at: $ROOT_VENV"
    "$PY311_DIR/bin/python3" -m venv "$ROOT_VENV"
else
    echo "[action_setup] Root venv already exists at: $ROOT_VENV"
fi

# Step 2: Set up per-bundle virtual environments and track installed actions
echo "[action_setup] Setting up action folders..."

ALLOWED_ACTIONS_FILE="$ACTIONS_DIR/.allowed_actions.txt"

# Require explicit action list
if [ ${#ACTIONS_TO_INSTALL[@]} -eq 0 ]; then
    echo "[action_setup] ERROR: No actions specified. Please provide action names as arguments."
    exit 1
fi

echo "[action_setup] Processing specified actions: ${ACTIONS_TO_INSTALL[*]}"
for action_name in "${ACTIONS_TO_INSTALL[@]}"; do
    bundle="$ACTIONS_DIR/$action_name"
    
    # Check if folder exists
    if [ ! -d "$bundle" ]; then
        echo "[action_setup] ERROR: Action folder not found: $bundle"
        continue
    fi
    
    # If bundle has install.sh, create venv if needed and run install.sh
    if [ -f "$bundle/install.sh" ]; then
        echo "[action_setup] Setting up $action_name"
        
        # Always recreate venv (remove if exists, then create fresh)
        echo "[action_setup] Creating/recreating venv for $action_name"
        rm -rf "$bundle/.venv"
        "$PY311_DIR/bin/python3" -m venv "$bundle/.venv"

        # Ensure bundle directory is owned by user and has proper permissions
        echo "[action_setup] Setting ownership and permissions for $action_name"
        sudo chown -R "$(whoami)" "$bundle"
        chmod -R 777 "$bundle"

        # Ensure bin files are executable
        if [ -d "$bundle/bin" ]; then
            echo "[action_setup] Ensuring bin files are executable for $action_name"
            find "$bundle/bin" -type f -exec chmod -v +x {} \;
        fi

        # Run install.sh inside the bundle venv
        echo "[action_setup] Running install.sh for $action_name"
        (cd "$bundle" && source .venv/bin/activate && bash ./install.sh)
    fi

    # Track allowed actions even when no install.sh is present
    if [ ! -f "$ALLOWED_ACTIONS_FILE" ]; then
        echo "[action_setup] Creating allowed actions file at $ALLOWED_ACTIONS_FILE"
        echo "$action_name" > "$ALLOWED_ACTIONS_FILE"
    elif ! grep -Fxq "$action_name" "$ALLOWED_ACTIONS_FILE"; then
        echo "$action_name" >> "$ALLOWED_ACTIONS_FILE"
        echo "[action_setup] Added $action_name to allowed actions file"
    fi
done

# Ensure proper ownership for default user
echo "[action_setup] Setting ownership for $ACTIONS_DIR to user:user"
sudo chown -R user:user "$ACTIONS_DIR"

echo "[action_setup] Done."