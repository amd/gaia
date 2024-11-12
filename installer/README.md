#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.

# GAIA Installer

## Running the installer

To run the installer, simply:
* Install [NSIS 3.10](https://prdownloads.sourceforge.net/nsis/nsis-3.10-setup.exe?download)
* Run `"C:\Program Files (x86)\NSIS\makensis.exe" Installer.nsi` to compile the installer with all features
* Open the exe

## Debugging

Debugging the installer could be ticky on a workflow since NSIS does not log anything that happens inside an `execWait` when running on a GitHub Workflow. To go around that, simply run the installer locally. To debug locally you have two options:

### Option 1: GUI installation
* Change all `ExecWait`s inside `Installer.nsi` to `Exec`. This will make sure terminals are not closed once something fails.
* Compile and run normally

### Option 2: Silent mode through terminal
* From a `Command Prompt` console, run `Gaia_Installer.exe /S`. All logs will be shown on the screen.


## Other notes

### NPU Installer

To manually compile the installer for the NPU version, you need to set the `OGA_TOKEN` environment variable to your GitHub token with access to the `oga-npu` repository. This is used to automatically download the NPU dependencies. You also need to set the `HF_TOKEN` environment variable to your Hugging Face token.

This is all done automatically and securely by our workflow and should ideally not be done manually. However, if you need to, here's how:

`"C:\Program Files (x86)\NSIS\makensis.exe" /DOGA_TOKEN=<token> /DHF_TOKEN=<token> /DMODE=NPU Installer.nsi`
