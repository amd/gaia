@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
@echo off

pip install pyinstaller
pyinstaller src\gaia\interface\widget.py ^
    --name "gaia" ^
    --windowed ^
    --collect-all gaia ^
    --collect-all llama_cpp ^
    --collect-all llama_index ^
    --collect-all lemonade ^
    --collect-all turnkeyml ^
    --collect-all turnkeyml_models ^
    --hidden-import=tiktoken_ext.openai_public ^
    --hidden-import=tiktoken_ext ^
    --hidden-import=llama_cpp ^
    --hidden-import=llama_index ^
    --hidden-import=lemonade ^
    --hidden-import=turnkeyml ^
    --hidden-import=turnkeyml_models ^
    --noconfirm ^
    --icon src\gaia\interface\img\gaia.ico ^
    --add-data "src/gaia/interface/settings.json;gaia/interface" ^
    --add-data "src/gaia/interface/img/gaia.ico;gaia/interface/img" ^
    --add-data "src/gaia/interface/img/gaia.png;." ^
    --add-data "%CONDA_PREFIX%/lib/site-packages/lemonade;lemonade" ^
    --add-data "%CONDA_PREFIX%/lib/site-packages/turnkeyml;turnkeyml" ^
    --add-data "%CONDA_PREFIX%/lib/site-packages/turnkeyml_models;turnkeyml_models" ^
    --onefile ^
    --debug=all

pause

@REM --collect-submodules llama_index
@REM --add-data "C:\Users\kalin\miniconda3\envs\oga-npu-2\Lib\site-packages\llama_index;llama_index" ^