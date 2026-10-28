@echo off
rem Opens Photography Tools using this folder's virtual environment if it exists.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m photo_sorter %*
) else (
    start "" pythonw -m photo_sorter %*
)
