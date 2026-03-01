@echo off
setlocal

echo [1/4] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not added to the system PATH.
    echo Please download it from python.org and check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

echo [2/4] Creating virtual environment (venv)...
python -m venv venv
if %errorlevel% neq 0 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
)

echo [3/4] Upgrading package manager (pip)...
call venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul

echo [4/4] Installing dependencies from requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo Installation completed successfully!
echo You can now start the application using: run.bat
echo ==========================================
pause