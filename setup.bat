@REM This script needs to be executed BEFORE attempting to run the NPU and
@REM assumes all dependencies are already installed from the transformers library.
@REM
@REM This script prompts the user to enter the path to the transformers directory,
@REM checks if the setup_phx.bat file exists in the specified directory, changes the
@REM current directory to the transformers directory, executes the setup_phx.bat
@REM file, and then returns to the original directory.

@echo off
set /p "local_path=Enter the path to the transformers directory: "

if not exist "%local_path%\setup_phx.bat" (
    echo setup_phx.bat not found in the specified directory.
    echo Please make sure the path is correct and the file exists.
    pause
    exit /b 1
)

echo %local_path%
pushd "%local_path%"
call setup_phx.bat
popd