# GAIA Installer

## Running the installer

To run the installer, simply:
* Install [NSIS 3.10](https://prdownloads.sourceforge.net/nsis/nsis-3.10-setup.exe?download)
* Run `"C:\Program Files (x86)\NSIS\makensis.exe" Installer.nsi` to compile
* Open the exe

## Debugging

Debugging the installer could be ticky on a workflow since NSIS does not log anything that happens inside an `execWait` when running on a GitHub Workflow. To go around that, simply run the installer locally. To debug locally you have two options:

### Option 1: GUI installation
* Change all `ExecWait`s inside `Installer.nsi` to `Exec`. This will make sure terminals are not closed once something fails.
* Compile and run normally

### Option 2: Silent mode through terminal
* From a `Command Prompt` console, run `Gaia_Installer.exe /S`. All logs will be shown on the screen.