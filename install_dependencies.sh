#!/usr/bin/env bash
# Strict mode for robust script execution
set -euo pipefail

echo "[1/4] Checking Python 3 installation..."
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 is not installed or not in the system PATH." >&2
    exit 1
fi

echo "[2/4] Creating virtual environment (venv)..."
# Catching specific missing package error on Debian/Ubuntu based systems
if ! python3 -m venv venv; then
    echo "ERROR: Failed to create virtual environment." >&2
    echo "Hint: On Ubuntu/Debian, you may need to run this command first:" >&2
    echo "      sudo apt install python3-venv" >&2
    exit 1
fi

echo "[3/4] Upgrading package manager (pip)..."
source venv/bin/activate
python3 -m pip install --upgrade pip --quiet

echo "[4/4] Installing dependencies from requirements.txt..."
pip install -r requirements.txt

echo ""
echo "=========================================="
echo "Installation completed successfully!"
echo "You can now start the application using: ./run.sh"
echo "=========================================="