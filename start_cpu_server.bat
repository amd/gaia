@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

@echo off
lemonade -i meta-llama/Meta-Llama-3.1-8B huggingface-load --dtype bfloat16 --device cpu serve --max-new-tokens 100