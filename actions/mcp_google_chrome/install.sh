#!/bin/bash
# Install dependencies for MCP tools action bundle

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Make bin scripts executable
chmod +x bin/* 2>/dev/null || true

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# Activate and install dependencies
source .venv/bin/activate

# Install fastmcp for MCP client functionality
pip install --upgrade pip
pip install fastmcp httpx

echo "MCP tools dependencies installed successfully"
