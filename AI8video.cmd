@echo off
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
"%PY%" -m ai8video %*
