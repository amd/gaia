@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
@echo off

pip install pyinstaller
pyinstaller src\gaia\interface\widget.py ^
    --name "gaia" ^
    --windowed ^
    --collect-all gaia ^
    --collect-all llama_cpp ^
    --collect-all llama_index ^
    --collect-all turnkeyml ^
    --hidden-import=tiktoken_ext.openai_public ^
    --hidden-import=tiktoken_ext ^
    --hidden-import=llama_cpp ^
    --hidden-import=llama_index ^
    --hidden-import=turnkeyml ^
    --noconfirm ^
    --icon src\gaia\interface\img\gaia.ico ^
    --add-data "src/gaia/interface/settings.json;gaia/interface" ^
    --add-data "src/gaia/interface/img/gaia.ico;gaia/interface/img" ^
    --add-data "src/gaia/interface/img/gaia.png;." ^
    --onefile ^
    --debug=all

pause