@echo off
REM ===========================================================================
REM  z-graphrag-k one-click launcher (Windows)
REM  Keep this file ASCII-only: cmd.exe parses .bat as GBK and mangles UTF-8.
REM  Just double-click this file, or run: start.bat
REM ===========================================================================
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto no_python

if exist ".venv\Scripts\python.exe" goto have_venv

echo [kgrag] First run: creating virtual environment...
python -m venv .venv
if errorlevel 1 goto venv_failed
echo [kgrag] Installing dependencies (may take a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto install_failed

:have_venv
".venv\Scripts\python.exe" start.py %*
if errorlevel 1 goto run_failed
goto end

:no_python
echo [ERROR] Python not found. Install Python 3.11+ and add it to PATH.
goto end

:venv_failed
echo [ERROR] Failed to create virtual environment.
goto end

:install_failed
echo [ERROR] Dependency installation failed. Run manually: pip install -e .
goto end

:run_failed
echo [ERROR] Application exited with an error. See the log above.

:end
echo.
pause
endlocal
