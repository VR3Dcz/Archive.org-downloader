#!/usr/bin/env python3
"""
Archive.org Downloader - Cross-Platform Build Script
Automates the packaging of the Python source code into a standalone executable
using PyInstaller. Ensures clean build environments and proper GUI configuration.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

# Configuration
APP_NAME = "ArchiveDownloader"
ENTRY_POINT = "archive_org_downloader.pyw"
REQUIRED_PACKAGES = ["pyinstaller", "requests"]

def check_dependencies():
    """Ensures all required build dependencies are installed."""
    print("[INFO] Verifying build dependencies...")
    try:
        import PyInstaller
    except ImportError:
        print("[ERROR] PyInstaller is not installed.")
        print("       Please run: pip install pyinstaller")
        sys.exit(1)

    try:
        import requests
    except ImportError:
        print("[ERROR] Requests library is not installed.")
        print("       Please run: pip install requests")
        sys.exit(1)

def clean_environment():
    """Removes previous build artifacts to ensure a pristine build state."""
    print("[INFO] Cleaning previous build artifacts...")
    directories_to_clean = ["build", "dist"]
    files_to_clean = [f"{APP_NAME}.spec"]

    for directory in directories_to_clean:
        dir_path = Path(directory)
        if dir_path.exists() and dir_path.is_dir():
            try:
                shutil.rmtree(dir_path)
                print(f"       Removed directory: {directory}/")
            except OSError as e:
                print(f"[ERROR] Failed to remove {directory}/: {e}")

    for file in files_to_clean:
        file_path = Path(file)
        if file_path.exists():
            try:
                os.remove(file_path)
                print(f"       Removed file: {file}")
            except OSError as e:
                print(f"[ERROR] Failed to remove {file}: {e}")

def run_build():
    """Executes the PyInstaller compilation process with strict GUI flags."""
    if not Path(ENTRY_POINT).exists():
        print(f"[ERROR] Entry point '{ENTRY_POINT}' not found in the current directory.")
        sys.exit(1)

    print(f"\n[INFO] Starting compilation for {sys.platform}...")

    # PyInstaller arguments construction
    args = [
        "pyinstaller",
        "--name", APP_NAME,
        "--noconfirm",           # Automatically replace existing build
        "--onedir",              # Create a one-folder bundle containing the executable
        "--windowed",            # Prevent a console window from appearing (GUI mode)
        "--clean",               # Clean PyInstaller cache and remove temporary files
        ENTRY_POINT
    ]

    try:
        # Execute PyInstaller safely as a subprocess
        result = subprocess.run(args, check=True, text=True)
        if result.returncode == 0:
            print(f"\n[SUCCESS] Build completed successfully!")

            output_dir = Path("dist") / APP_NAME
            if sys.platform == "win32":
                executable = output_dir / f"{APP_NAME}.exe"
            else:
                executable = output_dir / APP_NAME

            print(f"[INFO] Your compiled application is located at: {executable.absolute()}")
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Build process failed with exit code {e.returncode}.")
        sys.exit(1)
    except FileNotFoundError:
        print("[ERROR] PyInstaller command not found. Is it in your system PATH?")
        sys.exit(1)

def main():
    print(f"--- ArchiveDownloader Build Pipeline ---")
    check_dependencies()
    clean_environment()
    run_build()

if __name__ == "__main__":
    main()
