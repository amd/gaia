#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

# Packaging gaia.exe (Needs Update)

The section explains how to create a redistributable gaia.exe binary.

> Note: the `pip` commands in this section do not use the `-e` option on purpose. This is because all packages must be in `site-packages` to facilitate packaging. If you are running into problems, try `pip list` and make sure that all packages are installed into site-packages and were not installed in-place via `-e`. 

Pre-requisites:
1. `conda create -n gaia-exe python=3.11`
1. `conda activate gaia-exe`
1. Install lemonade with ort-genai support (clone genai, cd into genai): `pip install .[og]`
1. Install gaia (clone this repo, cd into gaia): `pip install .`
1. `pip install pyinstaller`
<!-- 1. Copy your `genai\src\lemonade\tools\ort_genai\models\Phi-3-mini-4k-instruct-onnx_int4_awq_block-128` folder into your `gaia-exe` environment, e.g., `miniconda3\envs\gaia-exe\Lib\site-packages\lemonade\tools\ort_genai\models\Phi-3-mini-4k-instruct-onnx_int4_awq_block-128` -->
<!-- 1. On `gaia\src\gaia\interface\settings.json` set `dev_mode` to `false` -->

> Note: if you make any changes to `gaia` you need to `pip install .` that package again to include the changes in your next build.

Build executable:
1. Go to the repo root: `cd gaia`
1. `conda activate gaia-exe`
1. To build a basic executable for development purposes: `build_exe.bat`

1. This creates an executable at `dist/gaia/gaia.exe`

> Note: the entire `dist/gaia` folder is required to run this basic .exe. See explanation of options below for more details.

Let's break down some of the options in use there:
- The `-n gaia` is the name of the resulting executable.
- The positional `src\gaia\interface\widget.py` points to the the script that the executable should run. This is the "entry point" for the application.
- The `--collect-all` options make sure that non-Python dependencies from specific projects come in. These could likely be refined to point to specific data files, but who has the time?
- The `--hidden-import` options are required for `llama-index` to work.
- The `--no-confirm` makes it so that if you re-build, it wont ask your permission to overwrite the contents of the `dist` and `build` directories halfway through.
- The `--icon` option sets the app's icon in File Explorer.

> Note: all of these options can be encoded into a `gaia.spec` file that can be built with `pyinstaller gaia.spec`. That would save people from needing to copy-paste the whole command, however it also provides for less flexibility. When the project becomes more mature, we recommend committing a `gaia.spec` file with the exact desired options encoded.

There are some additional options that should be considered:
- `--windowed` makes it so that a command prompt isn't launched alongside the GUI when `gaia.exe` is clicked. This is better for deployment, but worse for development.
- `--onefile` builds the entire application into a monolithic `gaia.exe` binary, instead of a `gaia` folder that contains `gaia.exe` and `_internal`. However, this monolithic binary has to be unpacked every time the user runs it, which takes about a minute. We recommend creating an installer instead, but this will do in a pinch.