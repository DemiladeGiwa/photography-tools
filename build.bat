@echo off
rem One-click build: makes dist\PhotographyTools\ (portable), a .zip to share,
rem and dist\PhotographyTools-<version>-Setup.exe if Inno Setup 6 is installed.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
)
echo Installing build requirements...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller send2trash windnd || goto :error
echo Running tests...
".venv\Scripts\python.exe" -m pytest -q || goto :error
".venv\Scripts\python.exe" scripts\build_standalone.py %*  || goto :error
echo.
echo Build finished - see the dist folder.
explorer dist
pause
exit /b 0
:error
echo.
echo Build failed - scroll up to see why.
pause
exit /b 1
