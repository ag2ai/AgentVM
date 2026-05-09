#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Base image (input) and baked image (output)
QCOW2_IN="${1:-$REPO_ROOT/docker_vm_data/Ubuntu.qcow2}"
QCOW2_OUT="${2:-$REPO_ROOT/docker_vm_data/Ubuntu.actions.qcow2}"

ACTIONS_ROOT="$REPO_ROOT/actions"
GUEST_ACTIONS="/home/user/actions"
GUEST_SCRIPT_SRC="$REPO_ROOT/actions/action_setup.sh"
DEFAULT_LOCAL_SERVER_DIR_SRC="$REPO_ROOT/agentvm/server"
LOCAL_SERVER_DIR_SRC="${LOCAL_SERVER_DIR_SRC:-$DEFAULT_LOCAL_SERVER_DIR_SRC}"
if [ ! -d "$LOCAL_SERVER_DIR_SRC" ] && [ -d "$DEFAULT_LOCAL_SERVER_DIR_SRC" ]; then
  echo "Warning: LOCAL_SERVER_DIR_SRC not found, falling back to: $DEFAULT_LOCAL_SERVER_DIR_SRC" >&2
  LOCAL_SERVER_DIR_SRC="$DEFAULT_LOCAL_SERVER_DIR_SRC"
fi
echo "Using server dir source: $LOCAL_SERVER_DIR_SRC" >&2
GUEST_SERVER_DIR="/home/user/server"
export DEBIAN_FRONTEND=noninteractive
export LIBGUESTFS_BACKEND=direct
export LIBGUESTFS_DEBUG=1
export LIBGUESTFS_TRACE=1

if [ ! -f "$QCOW2_IN" ]; then
  echo "Input QCOW2 not found: $QCOW2_IN" >&2
  exit 1
fi

if [ ! -d "$ACTIONS_ROOT" ]; then
  echo "Actions directory not found: $ACTIONS_ROOT" >&2
  exit 1
fi

if [ ! -f "$GUEST_SCRIPT_SRC" ]; then
  echo "Guest setup script not found: $GUEST_SCRIPT_SRC" >&2
  exit 1
fi

mkdir -p "$(dirname -- "$QCOW2_OUT")"

if [ "$QCOW2_IN" != "$QCOW2_OUT" ]; then
  cp -f "$QCOW2_IN" "$QCOW2_OUT"
fi

apt-get update -qq
apt-get install -y -qq libguestfs-tools qemu-utils qemu-system-x86 ca-certificates curl
virt-customize -a "$QCOW2_OUT" \
  --mkdir /home/user/actions \
  --mkdir "$GUEST_SERVER_DIR" \
  --run-command 'chown -R user:user /home/user/actions'

if [ -d "$LOCAL_SERVER_DIR_SRC" ]; then
  virt-copy-in -a "$QCOW2_OUT" \
    "$LOCAL_SERVER_DIR_SRC" \
    /home/user
  virt-customize -a "$QCOW2_OUT" \
    --run-command "chown -R user:user $GUEST_SERVER_DIR"
else
  echo "Warning: server dir not found, skipping: $LOCAL_SERVER_DIR_SRC" >&2
fi

# ------------------------------------------------------------------
# NOTE: Modify both the  copy-in and run-command to bake actions
# Copy action folders and action_setup.sh into the guest
virt-copy-in -a "$QCOW2_OUT" \
  "$ACTIONS_ROOT/file_reader" \
  "$ACTIONS_ROOT/text_web_browser" \
  "$ACTIONS_ROOT/str_replace_editor" \
  "$ACTIONS_ROOT/pandoc_converter" \
  "$ACTIONS_ROOT/action_setup.sh" \
  "$ACTIONS_ROOT/run_python" \
  "$ACTIONS_ROOT/execute_bash" \
  "$GUEST_ACTIONS"

# Execute action_setup.sh inside the guest
virt-customize -a "$QCOW2_OUT" \
  --run-command "bash $GUEST_ACTIONS/action_setup.sh file_reader text_web_browser str_replace_editor pandoc_converter run_python execute_bash"

# ------------------------------------------------------------------
# NOTE: Modify below to install more dependencies for actions if needed
# Pre-install Python packages
virt-customize -a "$QCOW2_OUT" \
  --run-command 'pip3 install -q pandas openpyxl python-docx xlsxwriter'

virt-customize -a "$QCOW2_OUT" \
  --run-command 'apt install xdotool -y'

echo "Done. Baked image: $QCOW2_OUT"

# docker run --rm -it   --privileged   --cap-add=SYS_ADMIN   -e LIBGUESTFS_BACKEND=direct   -e LIBGUESTFS_MEMSIZE=20480   -v /home/jjl7199/OSWorld:/workspace   python:3.11-slim bash
# bash -lc "chmod +x /workspace/bake_actions.sh && /workspace/bake_actions.sh /workspace/docker_vm_data/Ubuntu.qcow2 /workspace/docker_vm_data/Ubuntu.actions.qcow2"

