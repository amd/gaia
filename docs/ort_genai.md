#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

# Complete Installation of GAIA on Onnx Runtime Gen-AI (ORT-GenAI)

## Before you start
The following instructions can be used to do the full installation of GAIA from source and has been tested on Microsoft Windows OS and [Miniconda 24+](https://docs.anaconda.com/free/miniconda/).

1. Install the RyzenAI NPU software drivers [here](https://ryzenai.docs.amd.com/en/latest/inst.html)
1. The following was confirmed working with the following setup:
    * Windows 11, 24H2
    * Miniconda 24+
    * NPU Driver Version: `32.0.201.204`, `32.0.201.156`
    * ASUS ProArt (HN7306W) Laptop with Ryzen AI 9 HX 370 (STX)

## Installing third-party tools
1. Download and install [miniconda](https://docs.anaconda.com/miniconda/)
1. Download and install [Visual Studio Build Tools](https://aka.ms/vs/17/release/vs_BuildTools.exe).
    1. During installation, make sure to select "Desktop development with C++" workload.
    1. After installation, you may need to restart your computer.

## Installing and running ORT-GenAI
1. NOTE: ⚠️ DO THESE STEPS IN EXACTLY THIS ORDER USING THE SAME COMMAND SHELL AND CONDA VIRTUAL ENVIRONMENT ⚠️
1. Install GAIA [here](#installing-gaia)
1. Install Lemonade [here](#installing-lemonade)
1. Download and install models [here](#download-and-install-models)
1. Install wheels [here](#install-ort-genai-wheels)
1. Run a quick test to verify [here](#run-a-quick-lemonade-test)
1. Run `gaia`

### Installing GAIA
1. Clone GAIA repo: `git clone https://github.com/aigdat/gaia.git`
1. In the GAIA command window, do the following:
    1. Go to the GAIA root: `cd ./gaia`
    1. Create and activate a conda environment:
        1. `conda create -n gaiaenv python=3.10`
        1. `conda activate gaiaenv`
    1. Install GAIA package and dependencies:
        1. `pip install .`
        NOTE: If actively developing, use `pip install -e .` to enable editable mode and create links to sources instead.

### Installing Lemonade
1. Clone Lemonade repo: `git clone https://github.com/aigdat/genai`
1. In the same command window, do the following:
    1. `cd genai`
    1. Checkout branch with hyperparameter bug fixes: `git checkout ramkrishna2910/oga-hyperparameters`
        1. The following commit hash has been confirmed to work: `4f0be9a0397c8f1e3626abe7025413170f1fb0ca`
    1. `pip install .[oga-npu]`

### Download and Install Models
1. Download and unzip the 8/15 drop of OGA from [Pooja's OneDrive](https://amdcloud-my.sharepoint.com/:f:/g/personal/pooja_amd_com/EmZfEAsIkFhJkbyzzG-EHugBfRrRemxxJMf-KNJFYullmA?e=nxKbu4). We will refer to the unzipped directory as `amd_oga\`.
1. Download the 8/15 python wheels from [Pooja's OneDrive](https://amdcloud-my.sharepoint.com/:f:/r/personal/anup_amd_com/Documents/OGA/oga_llm_0815_wheels?csf=1&web=1&e=P3W9cX) `oga_llm_0815_wheels\`.

Next, we will set up the "C++ flow", since that helps to initialize our models and check that everything functions correctly on our laptops. 

> Note: to do this, you need a copy of `run_llm.exe`. It is not obvious at the time of this writing where you are supposed to get this from. Please contact Ganesh, Pooja (pooja.ganesh@amd.com) if you need a copy of `run_llm.exe`.

Prepare your model folders:
1. In `amd_oga\` you will see 3 "model folders": `llama3-8b-int4\`, `llama2-7b-int4\`, and `qwen1.5-7b-int4\`. We will repeat the same process for each of them.
1. Copy `run_llm.exe` into the model folder.
1. `cd MODEL_FOLDER` (ie, `cd amd_oga\llama3-8b-int4`)
1. Run the `MODEL_NAME.bat` script in the model folder (ie, `llama3.bat`)
1. This will accomplish two things for you:
    1. The required binaries (`bins` folder, DLLs, etc.) will be automatically moved into your model folder.
    1. A "smoke test" will run that shows a few prompts and responses, as well as the TTFT and tokens/second for each response. Make sure that these values are to your liking.
1. Copy your model folders (e.g. `llama3-8b-int4`) from `amd_oga\` to lemonade's package installation typically `C:\Users\<user>\miniconda3\envs\gaiaenv\Lib\site-packages\lemonade\tools\ort_genai\models\.`
NOTE: If `models` folder does not exist, just create it.

### Install ORT-GenAI Wheels
1. Install the wheels in `oga_llm_0815_wheels\` :
    1. `cd path\to\oga_llm_0815_wheels`
    1. `pip install onnxruntime_vitisai-1.19.0-cp310-cp310-win_amd64.whl`
    1. `pip install onnxruntime_genai-0.4.0.dev0-cp310-cp310-win_amd64.whl`
    1. `pip install voe-1.2.0-cp310-cp310-win_amd64.whl`

### Run a Quick Lemonade Test
1. To test basic functionality:
```
lemonade -i meta-llama/Meta-Llama-3-8B oga-load --device npu --dtype int4 llm-prompt -p "hello whats your name?" --max-new-tokens 15
```
Expected output:
```
Building "meta-llama_Meta-Llama-3-8B"
[Vitis AI EP] No. of Operators :   CPU   107 MATMULNBITS   195    MLP   640 
[Vitis AI EP] No. of Subgraphs :MATMULNBITS    65    MLP    32 
    ✓ Loading OnnxRuntime-GenAI model   
    ✓ Prompting LLM   

meta-llama/Meta-Llama-3-8B:
        <built-in function input> (executed 1x)
                Build dir:      C:\Users\jfowe/.cache/lemonade\meta-llama_Meta-Llama-3-8B    
                Status:         Successful build!
                                Dtype:  int4 
                                Device: npu 
                                Response:       hello whats your name? can my phone and pc work togth for that and then make callings
```

NOTE: Some of the steps above were borrowed from the lemonade (genai) repo documentation found [here](https://github.com/aigdat/genai/blob/main/docs/ort_genai.md).

