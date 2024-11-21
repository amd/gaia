@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

@echo off
setlocal enabledelayedexpansion

REM Set the path to the Python executable
set PYTHON_PATH=python

REM Set the path to the pylint executable
set PYLINT_PATH=pylint

REM Set the source directory
set SRC_DIR=src\gaia

REM Set the pylint configuration file
set PYLINT_CONFIG=.pylintrc

REM Set the disabled checks
set DISABLED_CHECKS=C0103,C0301,W0246,W0221,E1102,R0401,E0401,W0718

REM Exlude work-in-progress agents
set EXCLUDE=src\gaia\agents\Maven\app.py,src\gaia\agents\Neo\app.py,ui_form.py

REM Run black
echo Running black...
black installer plot src tests --config pyproject.toml

REM Run pylint
echo Running pylint...
%PYTHON_PATH% -m %PYLINT_PATH% %SRC_DIR% --rcfile %PYLINT_CONFIG% --disable %DISABLED_CHECKS% --ignore-paths %EXCLUDE%

REM Check the exit code
if %errorlevel% neq 0 (
    echo Linting failed with errors.
    exit /b %errorlevel%
) else (
    echo Linting completed successfully.
)

endlocal