@REM This script needs to be executed BEFORE attempting to run the NPU and
@REM assumes all dependencies are already installed from the transformers library.
@REM
@REM This script prompts the user to enter the path to the transformers directory
@REM and the device type to use. It then checks if the setup file exists in the 
@REM specified directory, changes the current directory to the transformers directory,
@REM executes the appropriate setup file based on the device type, and then returns
@REM to the original directory.

@echo off

REM Check if device type is provided as an argument
if "%~1"=="" (
    REM If not provided, ask the user
    set /p "device_type=Enter the device type (stx or phx, default is stx): "
    if "!device_type!"=="" set "device_type=stx"
) else (
    set "device_type=%~1"
)
echo "device_type=%device_type%"

REM Validate device type
if /i not "%device_type%"=="stx" if /i not "%device_type%"=="phx" (
    echo Invalid device type. Please use 'stx' or 'phx'.
    exit /b 1
)

set /p "local_path=Enter the path to the transformers directory: "

set "setup_file=setup_%device_type%.bat"

if not exist "%local_path%\%setup_file%" (
    echo %setup_file% not found in the specified directory.
    echo Please make sure the path is correct and the file exists.
    pause
    exit /b 1
)

echo Using device type: %device_type%
echo Transformers directory: %local_path%
echo Setup file: %setup_file%

pushd "%local_path%"
call %setup_file%
popd