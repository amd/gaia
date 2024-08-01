@echo off
call setup.bat
lemonade ryzenai-npu-load --device stx -c meta-llama/Llama-2-7b-chat-hf serve --max-new-tokens 600