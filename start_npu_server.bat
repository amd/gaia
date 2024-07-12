@echo off
call setup.bat
lemonade ryzenai-npu-load --device phx -c meta-llama/Llama-2-7b-chat-hf serve --max-new-tokens 600