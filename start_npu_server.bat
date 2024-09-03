@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

@echo off
lemonade -i meta-llama/Llama-2-7b-chat-hf dml-og-load --dtype int4 --device npu serve --max-new-tokens 600