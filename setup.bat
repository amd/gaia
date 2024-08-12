@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
@REM
@REM This script needs to be executed BEFORE attempting to run the NPU and
@REM assumes all dependencies are already installed from the transformers library.
@REM
@REM This script prompts the user to enter the path to the transformers directory
@REM and the device type to use. It then checks if the setup file exists in the 
@REM specified directory, changes the current directory to the transformers directory,
@REM executes the appropriate setup file based on the device type, and then returns
@REM to the original directory.
@REM 

@echo off

REM Check if device type is provided as an argument
set /p "device_type=Enter the device type (stx or phx, default is stx): "
if "%device_type%"=="" set "device_type=stx"
echo "device_type=%device_type%"

REM Validate device type
if /i not "%device_type%"=="stx" if /i not "%device_type%"=="phx" (
    echo Invalid device type. Please use 'stx' or 'phx'.
    exit /b 1
)

set /p "gaia_path=Enter the path to the gaia directory (default .\): "
if "%gaia_path%"=="" set "gaia_path=.\"

set /p "gaia_env=Enter the path to the gaia environment (default gaiavenv): "
if "%gaia_env%"=="" set "gaia_env=gaiavenv"

set /p "transformers_path=Enter the path to the transformers directory (default ..\transformers): "
if "%transformers_path%"=="" set "transformers_path=..\transformers"

set /p "transformers_env=Enter the path to the transformers environment (default ryzenai-transformers): "
if "%transformers_env%"=="" set "transformers_env=ryzenai-transformers"

set "setup_file=setup_%device_type%.bat"

if not exist "%transformers_path%\%setup_file%" (
    echo %setup_file% not found in the specified directory.
    echo Please make sure the path is correct and the file exists.
    exit /b 1
)
