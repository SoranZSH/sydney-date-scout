@echo off
REM ==========================================================
REM Sydney Date Scout -- wrapper for manual / portable use.
REM
REM KEEP THIS FILE ASCII-ONLY.
REM cmd.exe parses .bat files using the ACTIVE CONSOLE CODEPAGE, not the file's
REM own encoding. On a Chinese Windows that may be 936 (GBK) or 65001 (UTF-8)
REM depending on how the shell was started, so any non-ASCII byte in here --
REM including a hardcoded path such as D:\Download\<chinese-name>\ -- gets
REM mis-decoded, splits lines mid-token, and makes REM comments run as commands.
REM Observed symptoms: exit code 1, "'/d' is not recognized", and no log file
REM created at all. Hence: ASCII only, and derive the project directory from
REM this script's own location instead of writing the path out literally.
REM
REM The scheduled task registered by deploy\register-task.ps1 -UseSource calls
REM pythonw.exe DIRECTLY and does not go through this file (task actions are
REM stored as Unicode in the task XML, so they have no codepage problem).
REM
REM Logging is handled by the program itself -- run.py::_setup_log writes
REM data\out\scout.log -- so no shell redirection is needed here.
REM ==========================================================

setlocal

if "%PYTHON_EXE%"=="" set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
set "PROJECT_DIR=%~dp0.."

if not exist "%PYTHON_EXE%" (
    echo ERROR: python not found at "%PYTHON_EXE%"
    echo Set PYTHON_EXE before running, or edit the default path in this file.
    exit /b 1
)

cd /d "%PROJECT_DIR%" || exit /b 1
"%PYTHON_EXE%" run.py run --respect-interval

endlocal
