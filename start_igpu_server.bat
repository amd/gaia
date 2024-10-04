@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

@echo off
lemonade dml-og-load -c microsoft/Phi-3-mini-4k-instruct --dtype int4 --device igpu serve --max-new-tokens 300