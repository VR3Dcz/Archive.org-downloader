#!/usr/bin/env bash
set -euo pipefail

if [ ! -f "venv/bin/activate" ]; then
    echo "ERROR: Virtual environment not found." >&2
    echo "Please execute the installation script first: ./install.sh" >&2
    exit 1
fi

source venv/bin/activate

# Execute the GUI application and detach it from the terminal
python3 archive_org_downloader.pyw &