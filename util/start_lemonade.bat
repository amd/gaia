@echo off
setlocal enabledelayedexpansion

:: Default values
set MODEL=amd/Llama-3.2-1B-Instruct-awq-g128-int4-asym-fp16-onnx-hybrid
set BACKEND=oga
set DEVICE=hybrid
set DTYPE=int4
set MAX_TOKENS=100

:: Parse command line arguments
:parse_args
if "%1"=="" goto :run_server
if "%1"=="--model" set MODEL=%2 & shift & shift & goto :parse_args
if "%1"=="--backend" set BACKEND=%2 & shift & shift & goto :parse_args
if "%1"=="--device" set DEVICE=%2 & shift & shift & goto :parse_args
if "%1"=="--dtype" set DTYPE=%2 & shift & shift & goto :parse_args
if "%1"=="--max-tokens" set MAX_TOKENS=%2 & shift & shift & goto :parse_args
shift
goto :parse_args

:run_server
echo Starting Lemonade server with:
echo Model: %MODEL%
echo Backend: %BACKEND%
echo Device: %DEVICE%
echo Dtype: %DTYPE%
echo Max Tokens: %MAX_TOKENS%

python -c "from gaia.llm.lemonade_server import launch_lemonade_server; launch_lemonade_server(backend='%BACKEND%', checkpoint='%MODEL%', device='%DEVICE%', dtype='%DTYPE%', max_new_tokens=%MAX_TOKENS%, cli_mode=True)"