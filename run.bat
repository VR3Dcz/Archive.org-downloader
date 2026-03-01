@echo off
if not exist "venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found. 
    echo Please execute install.bat first.
    pause
    exit /b 1
)

:: Uses pythonw.exe to launch the GUI without keeping the console window open
start "" "venv\Scripts\pythonw.exe" "archive_org_downloader.pyw"