@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

@echo off

call setup.bat

echo Using device type: %device_type%
echo Gaia directory: %gaia_path%
echo Gaia environment: %gaia_env%
echo Transformers directory: %transformers_path%
echo Transformers environment: %transformers_env%
echo Setup file: %setup_file%

REM Second terminal: gaia using gaia env
start cmd /k "conda activate %gaia_env% && gaia"

REM First terminal: llm web server using ryzenai-transformers env
start cmd /k "conda activate %transformers_env% && ^
pushd %transformers_path% && ^
call %setup_file% && ^
if /i "%device_type%"=="stx" (set MLADF=2x4x4) && ^
echo MLADF is set to %MLADF% && ^
popd
call %gaia_path%\start_npu_server.bat && ^
"

echo LLM web server and GAIA have been launched.