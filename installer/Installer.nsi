; Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
; GAIA Installer Script

; Define command line parameters
!define /ifndef MODE "GENERIC"  ; Default to GENERIC mode if not specified
!define /ifndef OGA_TOKEN ""  ; Default to empty string if not specified
!define /ifndef OGA_URL "https://api.github.com/repos/aigdat/ryzenai-sw-ea/contents/"
!define /ifndef RYZENAI_FOLDER "ryzen_ai_13_ga"
!define /ifndef NPU_DRIVER_ZIP "NPU_RAI1.3.zip"
!define /ifndef NPU_DRIVER_VERSION "32.0.203.240"
!define /ifndef LEMONADE_VERSION "v6.0.2"

; Command line usage:
;  gaia-windows-setup.exe [/S] [/DMODE=GENERIC|NPU|HYBRID] [/DCI=ON] [/D=<installation directory>]
;    /S - Silent install with no user interface
;    /DMODE=X - Set installation mode (GENERIC, NPU, or HYBRID)
;    /D=<path> - Set installation directory (must be last parameter)

; Define main variables
Name "GAIA"

InstallDir "$LOCALAPPDATA\GAIA"

; Include modern UI elements
!include "MUI2.nsh"

; Include LogicLib
!include LogicLib.nsh

; Include Sections for RadioButton functionality
!include "Sections.nsh"

; Include nsDialogs for custom pages
!include "nsDialogs.nsh"

; Enable StrLoc function
!include StrFunc.nsh
${StrLoc}

; For command-line parameter parsing
!include FileFunc.nsh
!insertmacro GetParameters
!insertmacro GetOptions

; Include version information
!if /FileExists "version.nsh"
  !include "version.nsh"
!else
  ; Fallback empty string if version.nsh is not available
  !define GAIA_VERSION ""
!endif

OutFile "gaia-windows-setup.exe"
!define MUI_WELCOMEFINISHPAGE_BITMAP ".\img\welcome_npu.bmp"

; Define variables for the welcome page image

; Define the GAIA_STRING variable
Var GAIA_STRING
Var SELECTED_MODE

; Variables for CPU detection
Var cpuName
Var isCpuSupported
Var ryzenAiPos
Var seriesStartPos
Var currentChar
Var Dialog
Var Label

; Component section descriptions
LangString DESC_GenericSec 1033 "Standard GAIA installation with CPU-only execution. Works on all systems."
LangString DESC_NPUSec 1033 "GAIA with NPU acceleration for optimized on-device AI. Requires Ryzen AI 300-series processors."
LangString DESC_HybridSec 1033 "GAIA with Hybrid execution mode which uses both NPU and iGPU for improved performance. Requires Ryzen AI 300-series processors."

; Warning message for incompatible processors
Function WarningPage
  ${If} $isCpuSupported != "true"
    !insertmacro MUI_HEADER_TEXT "Hardware Compatibility Warning" "Your processor does not support NPU/Hybrid modes"
    nsDialogs::Create 1018
    Pop $Dialog

    ; Create warning message with detected processor and contact info
    ${NSD_CreateLabel} 0 0 100% 140u "Detected Processor:$\n$cpuName$\nGAIA's NPU and Hybrid modes are currently only supported on AMD Ryzen AI 300-series processors.$\n$\nYou can:$\n1. Cancel the installation if you intended to use NPU/Hybrid features$\n2. Continue installation with Generic mode, which works on all systems with Ollama$\n$\n$\nFor more information, contact us at gaia@amd.com"
    Pop $Label
    SetCtlColors $Label "" "transparent"

    nsDialogs::Show
  ${EndIf}
FunctionEnd

; Using SectionGroup without /e flag since we're handling radio buttons manually
SectionGroup /e "Installation Mode" InstallModeGroup
  Section "Generic Mode" GenericSec
  SectionEnd

  Section /o "NPU Mode" NPUSec
  SectionEnd

  Section /o "Hybrid Mode" HybridSec
  SectionEnd
SectionGroupEnd

; Variable to track whether to install RAUX
Var InstallRAUX

; Custom finish page variables
Var RunGAIAUICheckbox
Var RunGAIACLICheckbox
Var RunRAUXCheckbox

Function .onInit
  ; Default to Hybrid mode
  StrCpy $GAIA_STRING "GAIA - Ryzen AI Hybrid Mode, ver: ${GAIA_VERSION}"
  StrCpy $SELECTED_MODE "HYBRID"

  ; Check for command-line mode parameter
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/MODE=" $0
  ${IfNot} ${Errors}
    ${If} $0 == "GENERIC"
    ${OrIf} $0 == "NPU"
    ${OrIf} $0 == "HYBRID"
      StrCpy $SELECTED_MODE $0
      DetailPrint "Installation mode set from command line: $SELECTED_MODE"

      ; Update GAIA_STRING based on mode
      ${If} $SELECTED_MODE == "HYBRID"
        StrCpy $GAIA_STRING "GAIA - Ryzen AI Hybrid Mode, ver: ${GAIA_VERSION}"
      ${ElseIf} $SELECTED_MODE == "NPU"
        StrCpy $GAIA_STRING "GAIA - Ryzen AI NPU Mode, ver: ${GAIA_VERSION}"
      ${ElseIf} $SELECTED_MODE == "GENERIC"
        StrCpy $GAIA_STRING "GAIA - Generic Mode, ver: ${GAIA_VERSION}"
      ${EndIf}
    ${EndIf}
  ${EndIf}

  ; Store the default selection for radio buttons
  StrCpy $R9 ${HybridSec}

  ; Select mode based on SELECTED_MODE
  ${If} $SELECTED_MODE == "GENERIC"
    ; Select Generic section
    SectionGetFlags ${GenericSec} $0
    IntOp $0 $0 | ${SF_SELECTED}
    SectionSetFlags ${GenericSec} $0

    ; Deselect others
    SectionGetFlags ${NPUSec} $0
    IntOp $0 $0 & ${SECTION_OFF}
    SectionSetFlags ${NPUSec} $0

    SectionGetFlags ${HybridSec} $0
    IntOp $0 $0 & ${SECTION_OFF}
    SectionSetFlags ${HybridSec} $0

    ; Update radio button variable
    StrCpy $R9 ${GenericSec}
  ${ElseIf} $SELECTED_MODE == "NPU"
    ; Select NPU section
    SectionGetFlags ${NPUSec} $0
    IntOp $0 $0 | ${SF_SELECTED}
    SectionSetFlags ${NPUSec} $0

    ; Deselect others
    SectionGetFlags ${GenericSec} $0
    IntOp $0 $0 & ${SECTION_OFF}
    SectionSetFlags ${GenericSec} $0

    SectionGetFlags ${HybridSec} $0
    IntOp $0 $0 & ${SECTION_OFF}
    SectionSetFlags ${HybridSec} $0

    ; Update radio button variable
    StrCpy $R9 ${NPUSec}
  ${Else} ; Default to HYBRID
    ; Select Hybrid mode as default
    SectionGetFlags ${HybridSec} $0
    IntOp $0 $0 | ${SF_SELECTED}
    SectionSetFlags ${HybridSec} $0

    ; Deselect the others
    SectionGetFlags ${GenericSec} $0
    IntOp $0 $0 & ${SECTION_OFF}
    SectionSetFlags ${GenericSec} $0

    SectionGetFlags ${NPUSec} $0
    IntOp $0 $0 & ${SECTION_OFF}
    SectionSetFlags ${NPUSec} $0

    ; Update radio button variable
    StrCpy $R9 ${HybridSec}
  ${EndIf}

  ; Check CPU name to determine if NPU/Hybrid sections should be enabled
  DetailPrint "Checking CPU model..."

  ; Use registry query to get CPU name
  nsExec::ExecToStack 'reg query "HKEY_LOCAL_MACHINE\HARDWARE\DESCRIPTION\System\CentralProcessor\0" /v ProcessorNameString'
  Pop $0 ; Return value
  Pop $cpuName ; Output (CPU name)
  DetailPrint "Detected CPU: $cpuName"

  ; Check if CPU name contains "Ryzen AI" and a 3-digit number starting with 3
  StrCpy $isCpuSupported "false" ; Initialize CPU allowed flag to false

  ${StrLoc} $ryzenAiPos $cpuName "Ryzen AI" ">"
  ${If} $ryzenAiPos != ""
    ; Found "Ryzen AI", now look for 3xx series
    ${StrLoc} $seriesStartPos $cpuName " 3" ">"
    ${If} $seriesStartPos != ""
      ; Check if the character after "3" is a digit (first digit of model number)
      StrCpy $currentChar $cpuName 1 $seriesStartPos+2
      ${If} $currentChar >= "0"
        ${AndIf} $currentChar <= "9"
        ; Check if the character after that is also a digit (second digit of model number)
        StrCpy $currentChar $cpuName 1 $seriesStartPos+3
        ${If} $currentChar >= "0"
          ${AndIf} $currentChar <= "9"
          ; Found a complete 3-digit number starting with 3
          StrCpy $isCpuSupported "true"
          DetailPrint "Detected Ryzen AI 3xx series processor"
        ${EndIf}
      ${EndIf}
    ${EndIf}
  ${EndIf}

  DetailPrint "CPU is compatible with Ryzen AI NPU/Hybrid software: $isCpuSupported"

  ; If CPU is not compatible, disable NPU and Hybrid sections and force Generic
  ${If} $isCpuSupported != "true"
    ; Disable NPU section (make it unselectable)
    SectionGetFlags ${NPUSec} $0
    IntOp $0 $0 & ${SECTION_OFF}    ; Turn off selection
    IntOp $0 $0 | ${SF_RO}          ; Make it read-only
    SectionSetFlags ${NPUSec} $0

    ; Disable Hybrid section (make it unselectable)
    SectionGetFlags ${HybridSec} $0
    IntOp $0 $0 & ${SECTION_OFF}    ; Turn off selection
    IntOp $0 $0 | ${SF_RO}          ; Make it read-only
    SectionSetFlags ${HybridSec} $0

    ; Force Generic selection
    SectionGetFlags ${GenericSec} $0
    IntOp $0 $0 | ${SF_SELECTED}    ; Turn on selection
    SectionSetFlags ${GenericSec} $0

    ; Update stored radio button variable for incompatible CPUs
    StrCpy $R9 ${GenericSec}

    ; Update variables for Generic mode
    StrCpy $SELECTED_MODE "GENERIC"
    StrCpy $GAIA_STRING "GAIA - Generic Mode, ver: ${GAIA_VERSION}"

    ; Make a note in the detail log
    DetailPrint "CPU not compatible with Ryzen AI, forcing Generic mode"
  ${EndIf}

  ; Initialize InstallRAUX to 0 (unchecked)
  StrCpy $InstallRAUX "0"

  ; Hide RAUX option if not installed
  ${If} $InstallRAUX != "1"
    !define MUI_FINISHPAGE_SHOWREADME2 ""
  ${EndIf}
FunctionEnd

; Define constants
!define PRODUCT_NAME "GAIA"
!define GITHUB_REPO "https://github.com/aigdat/gaia.git"
!define EMPTY_FILE_NAME "empty_file.txt"
!define ICON_FILE "../src/gaia/interface/img/gaia.ico"

; Custom page for RAUX installation option
; Function RAUXOptionsPage
;   !insertmacro MUI_HEADER_TEXT "Additional Components" "Choose additional components to install"
;   nsDialogs::Create 1018
;   Pop $0
;
;   ${NSD_CreateCheckbox} 10 10 100% 12u "Install AMD RAUX [beta]"
;   Pop $1
;   ${NSD_SetState} $1 $InstallRAUX
;   SetCtlColors $1 "" "transparent"
;
;   ${NSD_CreateLabel} 25 30 100% 40u "RAUX (an Open-WebUI fork) is AMD's new UI for interacting with AI models.$\nIt provides a chat interface similar to ChatGPT and other AI assistants.$\nThis feature is currently in beta."
;   Pop $2
;   SetCtlColors $2 "" "transparent"
;
;   nsDialogs::Show
; FunctionEnd
;
; Function RAUXOptionsLeave
;   ${NSD_GetState} $1 $InstallRAUX
; FunctionEnd

; Custom finish page
Function CustomFinishPage
  nsDialogs::Create 1018
  Pop $Dialog

  ${NSD_CreateLabel} 0 20 100% 40u "$GAIA_STRING has been installed successfully! A shortcut has been added to your Desktop.$\n$\n$\nWhat would you like to do next?"
  Pop $0

  ${NSD_CreateCheckbox} 20 100 100% 12u "Run GAIA UI"
  Pop $RunGAIAUICheckbox

  ${NSD_CreateCheckbox} 20 120 100% 12u "Run GAIA CLI"
  Pop $RunGAIACLICheckbox

  ${If} $InstallRAUX == "1"
    ${NSD_CreateCheckbox} 20 140 100% 12u "Run RAUX"
    Pop $RunRAUXCheckbox
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function CustomFinishLeave
  ${NSD_GetState} $RunGAIAUICheckbox $0
  ${If} $0 == ${BST_CHECKED}
    Call RunGAIAUI
  ${EndIf}

  ${NSD_GetState} $RunGAIACLICheckbox $0
  ${If} $0 == ${BST_CHECKED}
    Call RunGAIACLI
  ${EndIf}

  ${If} $InstallRAUX == "1"
    ${NSD_GetState} $RunRAUXCheckbox $0
    ${If} $0 == ${BST_CHECKED}
      Call RunRAUX
    ${EndIf}
  ${EndIf}
FunctionEnd

; MUI Settings
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
Page custom WarningPage
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
; Page custom RAUXOptionsPage RAUXOptionsLeave
!insertmacro MUI_PAGE_INSTFILES
Page custom CustomFinishPage CustomFinishLeave
!insertmacro MUI_LANGUAGE "English"

; Set the installer icon
Icon ${ICON_FILE}

; Language settings
LangString MUI_TEXT_WELCOME_INFO_TITLE 1033 "Welcome to the GAIA Installer"
LangString MUI_TEXT_WELCOME_INFO_TEXT 1033 "This wizard will install $GAIA_STRING on your computer."
LangString MUI_TEXT_DIRECTORY_TITLE 1033 "Select Installation Directory"
LangString MUI_TEXT_INSTALLING_TITLE 1033 "Installing $GAIA_STRING"
LangString MUI_TEXT_FINISH_TITLE 1033 "Installation Complete"
LangString MUI_TEXT_FINISH_SUBTITLE 1033 "Thank you for installing GAIA!"
LangString MUI_TEXT_ABORT_TITLE 1033 "Installation Aborted"
LangString MUI_TEXT_ABORT_SUBTITLE 1033 "Installation has been aborted."
LangString MUI_BUTTONTEXT_FINISH 1033 "Finish"
LangString MUI_TEXT_LICENSE_TITLE 1033 "License Agreement"
LangString MUI_TEXT_LICENSE_SUBTITLE 1033 "Please review the license terms before installing GAIA."

; Insert the description macros
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${GenericSec} $(DESC_GenericSec)
  !insertmacro MUI_DESCRIPTION_TEXT ${NPUSec} $(DESC_NPUSec)
  !insertmacro MUI_DESCRIPTION_TEXT ${HybridSec} $(DESC_HybridSec)
!insertmacro MUI_FUNCTION_DESCRIPTION_END

; Function to update selected mode when selection changes
Function .onSelChange
  ; Use RadioButton macros to enforce mutual exclusivity
  !insertmacro StartRadioButtons $R9
  !insertmacro RadioButton ${GenericSec}
  !insertmacro RadioButton ${NPUSec}
  !insertmacro RadioButton ${HybridSec}
  !insertmacro EndRadioButtons

  ; Update variables based on selection
  SectionGetFlags ${GenericSec} $0
  IntOp $0 $0 & ${SF_SELECTED}
  ${If} $0 == ${SF_SELECTED}
    StrCpy $SELECTED_MODE "GENERIC"
    StrCpy $GAIA_STRING "GAIA - Generic Mode, ver: ${GAIA_VERSION}"
  ${EndIf}

  SectionGetFlags ${NPUSec} $0
  IntOp $0 $0 & ${SF_SELECTED}
  ${If} $0 == ${SF_SELECTED}
    StrCpy $SELECTED_MODE "NPU"
    StrCpy $GAIA_STRING "GAIA - Ryzen AI NPU Mode, ver: ${GAIA_VERSION}"
  ${EndIf}

  SectionGetFlags ${HybridSec} $0
  IntOp $0 $0 & ${SF_SELECTED}
  ${If} $0 == ${SF_SELECTED}
    StrCpy $SELECTED_MODE "HYBRID"
    StrCpy $GAIA_STRING "GAIA - Ryzen AI Hybrid Mode, ver: ${GAIA_VERSION}"
  ${EndIf}

  DetailPrint "Selected mode changed to: $SELECTED_MODE"
FunctionEnd

; Define a section for the installation
Section "-Install Main Components" SEC01
  ; Remove FileOpen/FileWrite for log file, replace with DetailPrint
  DetailPrint "*** INSTALLATION STARTED ***"
  DetailPrint "------------------------"
  DetailPrint "- Installation Section -"
  DetailPrint "------------------------"
  ; Check if directory exists before proceeding
  IfFileExists "$INSTDIR\*.*" 0 continue_install
    ${IfNot} ${Silent}
      MessageBox MB_YESNO "An existing GAIA installation was found at $INSTDIR.$\n$\nWould you like to remove it and continue with the installation?" IDYES remove_dir
      ; If user selects No, show exit message and quit the installer
      MessageBox MB_OK "Installation cancelled. Exiting installer..."
      DetailPrint "Installation cancelled by user"
      Quit
    ${Else}
      GoTo remove_dir
    ${EndIf}

  remove_dir:
    ; Attempt conda remove of the env, to help speed things up
    ExecWait 'conda env remove -yp "$INSTDIR\gaia_env"'
    ; Try to remove directory and verify it was successful
    RMDir /r "$INSTDIR"
    DetailPrint "- Deleted all contents of install dir"

    IfFileExists "$INSTDIR\*.*" 0 continue_install
      ${IfNot} ${Silent}
        MessageBox MB_OK "Unable to remove existing installation. Please close any applications using GAIA and try again."
      ${EndIf}
      DetailPrint "Failed to remove existing installation"
      Quit

  continue_install:
    ; Create fresh directory
    CreateDirectory "$INSTDIR"

    ; Set the output path for future operations
    SetOutPath "$INSTDIR"

    DetailPrint "Starting '$GAIA_STRING' Installation..."
    DetailPrint 'Configuration:'
    DetailPrint '  Install Dir: $INSTDIR'
    DetailPrint '  Mode: $SELECTED_MODE'
    DetailPrint '  OGA URL: ${OGA_URL}'
    DetailPrint '  Ryzen AI Folder: ${RYZENAI_FOLDER}'
    DetailPrint '  Recommended NPU Driver Version: ${NPU_DRIVER_VERSION}'
    DetailPrint '-------------------------------------------'

    ; Pack GAIA into the installer
    ; Exclude hidden files (like .git, .gitignore) and the installation folder itself
    File /r /x installer /x .* /x ..\*.pyc ..\*.* download_lfs_file.py npu_driver_utils.py amd_install_kipudrv.bat install.bat
    DetailPrint "- Packaged GAIA repo"

    ; Check if conda is available
    ExecWait 'where conda' $2
    DetailPrint "- Checked if conda is available"

    ; If conda is not found, show a message and exit
    ; Otherwise, continue with the installation
    StrCmp $2 "0" check_mode conda_not_available

    conda_not_available:
      DetailPrint "- Conda not installed."
      ${IfNot} ${Silent}
        MessageBox MB_YESNO "Conda is not installed. Would you like to install Miniconda?" IDYES install_miniconda IDNO exit_installer
      ${Else}
        GoTo install_miniconda
      ${EndIf}

    exit_installer:
      DetailPrint "- Something went wrong. Exiting installer"
      Quit

    install_miniconda:
      DetailPrint "-------------"
      DetailPrint "- Miniconda -"
      DetailPrint "-------------"
      DetailPrint "- Downloading Miniconda installer..."
      ExecWait 'curl -s -o "$TEMP\Miniconda3-latest-Windows-x86_64.exe" "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"'

      ; Install Miniconda silently
      ExecWait '"$TEMP\Miniconda3-latest-Windows-x86_64.exe" /InstallationType=JustMe /AddToPath=1 /RegisterPython=0 /S /D=$PROFILE\miniconda3' $2
      ; Check if Miniconda installation was successful
      ${If} $2 == 0
        DetailPrint "- Miniconda installation successful"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Miniconda has been successfully installed."
        ${EndIf}

        StrCpy $R1 "$PROFILE\miniconda3\Scripts\conda.exe"
        GoTo check_mode

      ${Else}
        DetailPrint "- Miniconda installation failed"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Error: Miniconda installation failed. Installation will be aborted."
        ${EndIf}
        GoTo exit_installer
      ${EndIf}

    check_mode:
        ${If} $SELECTED_MODE == "GENERIC"
          GoTo check_ollama
        ${Else}
          GoTo check_lemonade
        ${EndIf}

    check_lemonade:
      DetailPrint "------------"
      DetailPrint "- Lemonade -"
      DetailPrint "------------"

      ; Check if lemonade is available by trying to run it
      nsExec::ExecToStack 'lemonade --version'
      Pop $2  ; Return value
      Pop $3  ; Command output
      DetailPrint "- Checked if lemonade is available (return code: $2)"

      ; If lemonade is not found (return code != 0), show message and proceed with installation
      ${If} $2 != "0"
        DetailPrint "- Lemonade not installed or not in PATH"
        ${IfNot} ${Silent}
          MessageBox MB_YESNO "Lemonade is required but not installed.$\n$\nWould you like to install Lemonade now?" IDYES install_lemonade IDNO skip_lemonade
        ${Else}
          GoTo skip_lemonade
        ${EndIf}
      ${Else}
        DetailPrint "- Lemonade is already installed"
        GoTo create_env
      ${EndIf}

    install_lemonade:
      ; Check if file already exists and delete it first
      IfFileExists "$TEMP\Lemonade_Server_Installer.exe" 0 download_lemonade
        Delete "$TEMP\Lemonade_Server_Installer.exe"

      download_lemonade:
        DetailPrint "- Downloading Lemonade installer..."
        ; Use nsExec::ExecToStack to capture the output and error code
        nsExec::ExecToStack 'curl -L -f -v --retry 3 --retry-delay 2 -o "$TEMP\Lemonade_Server_Installer.exe" "https://github.com/onnx/turnkeyml/releases/download/${LEMONADE_VERSION}/Lemonade_Server_Installer.exe"'
        Pop $0  ; Return value
        Pop $1  ; Command output
        DetailPrint "- Curl return code: $0"
        DetailPrint "- Curl output: $1"

      ; Check if download was successful
      IfFileExists "$TEMP\Lemonade_Server_Installer.exe" lemonade_download_success lemonade_download_failed

      lemonade_download_failed:
        DetailPrint "- Failed to download Lemonade installer"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Failed to download Lemonade installer. Please install Lemonade manually from https://github.com/onnx/turnkeyml/releases after installation completes."
        ${EndIf}
        GoTo skip_lemonade

      lemonade_download_success:
        DetailPrint "- Download successful ($TEMP\Lemonade_Server_Installer.exe), installing Lemonade..."
        ExecWait '"$TEMP\Lemonade_Server_Installer.exe" /Extras=hybrid' $2

        ${If} $2 == 0
          DetailPrint "- Lemonade installation successful"
          ${IfNot} ${Silent}
            MessageBox MB_OK "Lemonade has been successfully installed."
          ${EndIf}
        ${Else}
          DetailPrint "- Lemonade installation failed with error code: $2"
          DetailPrint "- Please install Lemonade manually after GAIA installation"
          ${IfNot} ${Silent}
            MessageBox MB_OK "Lemonade installation failed. Please install Lemonade manually from https://github.com/onnx/turnkeyml/releases and try again.$\n$\nError code: $2"
          ${EndIf}
          GoTo exit_installer
        ${EndIf}

        ; Clean up the downloaded installer
        Delete "$TEMP\Lemonade_Server_Installer.exe"
        GoTo create_env

    skip_lemonade:
      DetailPrint "- Continuing installation without Lemonade"
      GoTo create_env

    check_ollama:
      DetailPrint "----------"
      DetailPrint "- Ollama -"
      DetailPrint "----------"

      ; Check if ollama is available only for GENERIC mode
      ${If} $SELECTED_MODE == "GENERIC"
        ExecWait 'where ollama' $2
        DetailPrint "- Checked if ollama is available"

        ; If ollama is not found, show a message and exit
        StrCmp $2 "0" create_env ollama_not_available
      ${Else}
        DetailPrint "- Skipping ollama check for $SELECTED_MODE mode"
        GoTo create_env
      ${EndIf}

    ollama_not_available:
      DetailPrint "- Ollama not installed."
      ${IfNot} ${Silent}
        MessageBox MB_YESNO "Ollama is required but not installed. Would you like to install Ollama now? You can install it later from ollama.com/download" IDYES install_ollama IDNO skip_ollama
      ${EndIf}
      GoTo skip_ollama

    install_ollama:
      DetailPrint "- Downloading Ollama installer..."
      ExecWait 'curl -L -o "$TEMP\OllamaSetup.exe" "https://ollama.com/download/OllamaSetup.exe"'

      ; Check if download was successful
      IfFileExists "$TEMP\OllamaSetup.exe" download_success download_failed

      download_failed:
        DetailPrint "- Failed to download Ollama installer"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Failed to download Ollama installer. Please install Ollama manually from ollama.com/download after installation completes."
        ${EndIf}
        GoTo skip_ollama

      download_success:
        DetailPrint "- Download successful ($TEMP\OllamaSetup.exe), installing Ollama..."
        ; Run with elevated privileges and wait for completion
        ExecWait '"$TEMP\OllamaSetup.exe" /SILENT' $2

        ${If} $2 == 0
          DetailPrint "- Ollama installation successful"
          ${IfNot} ${Silent}
            MessageBox MB_OK "Ollama has been successfully installed."
          ${EndIf}
        ${Else}
          DetailPrint "- Ollama installation failed with error code: $2"
          DetailPrint "- Please install Ollama manually after GAIA installation"
          ${IfNot} ${Silent}
            MessageBox MB_OK "Ollama installation failed. Please install Ollama manually from ollama.com/download after installation completes.$\n$\nError code: $2"
          ${EndIf}
        ${EndIf}

        ; Clean up the downloaded installer
        Delete "$TEMP\OllamaSetup.exe"
        GoTo skip_ollama

    skip_ollama:
      DetailPrint "- Continuing installation without Ollama"
      GoTo create_env

    create_env:

      DetailPrint "---------------------"
      DetailPrint "- Conda Environment -"
      DetailPrint "---------------------"

      DetailPrint "- Initializing conda..."
      ; Use the appropriate conda executable
      ${If} $R1 == ""
        StrCpy $R1 "conda"
      ${EndIf}
      ; Initialize conda (needed for systems where conda was previously installed but not initialized)
      nsExec::ExecToLog '$R1 init'

      DetailPrint "- Creating a Python 3.10 environment named 'gaia_env' in the installation directory: $INSTDIR..."
      ExecWait '$R1 create -p "$INSTDIR\gaia_env" python=3.10 -y' $R0

      ; Check if the environment creation was successful (exit code should be 0)
      StrCmp $R0 0 install_ffmpeg env_creation_failed

    env_creation_failed:
      DetailPrint "- ERROR: Environment creation failed"
      ; Display an error message and exit
      ${IfNot} ${Silent}
        MessageBox MB_OK "ERROR: Failed to create the Python environment. Installation will be aborted."
      ${EndIf}
      Quit

    install_ffmpeg:
      DetailPrint "----------"
      DetailPrint "- FFmpeg -"
      DetailPrint "----------"

      DetailPrint "- Checking if FFmpeg is already installed..."
      nsExec::ExecToStack 'where ffmpeg'
      Pop $R0  ; Return value
      Pop $R1  ; Command output

      ${If} $R0 == 0
        DetailPrint "- FFmpeg is already installed"
      ${Else}
        DetailPrint "- Installing FFmpeg using winget..."
        nsExec::ExecToLog 'winget install ffmpeg'
        Pop $R0  ; Return value
        Pop $R1  ; Command output
        DetailPrint "- FFmpeg installation return code: $R0"
        DetailPrint "- FFmpeg installation output:"
        DetailPrint "$R1"
      ${EndIf}
      GoTo install_ryzenai_driver

    install_ryzenai_driver:
      DetailPrint "--------------------------"
      DetailPrint "- Ryzen AI Driver Update -"
      DetailPrint "--------------------------"

      ${If} $SELECTED_MODE == "NPU"
      ${OrIf} $SELECTED_MODE == "HYBRID"
        ; If in silent mode, skip driver update
        ${If} ${Silent}
          GoTo install_gaia
        ${EndIf}

        DetailPrint "- Checking NPU driver version..."
        nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" npu_driver_utils.py --get-version'
        Pop $2 ; Exit code
        Pop $3 ; Command output (driver version)
        DetailPrint "- Driver version: $3"

        ; Check if the command was successful and a driver version was found
        ${If} $2 != "0"
        ${OrIf} $3 == ""
          DetailPrint "- Failed to retrieve current NPU driver version"
          StrCpy $3 "Unknown"
        ${EndIf}

        ; Get only the last line of $3 if it contains multiple lines
        StrCpy $4 $3 ; Copy $3 to $4 to preserve original value
        StrCpy $5 "" ; Initialize $5 as an empty string
        ${Do}
          ${StrLoc} $6 $4 "$\n" ">" ; Find the next newline character
          ${If} $6 == "" ; If no newline found, we're at the last line
            StrCpy $5 $4 ; Copy the remaining text to $5
            ${Break} ; Exit the loop
          ${Else}
            StrCpy $5 $4 "" $6 ; Copy the text after the newline to $5
            IntOp $6 $6 + 1 ; Move past the newline character
            StrCpy $4 $4 "" $6 ; Remove the processed part from $4
          ${EndIf}
        ${Loop}
        StrCpy $3 $5 ; Set $3 to the last line

        ${If} $3 == "Unknown"
          MessageBox MB_YESNO "WARNING: Current driver could not be identified. If you run into issues, please install the recommended driver version (${NPU_DRIVER_VERSION}) or reach out to gaia@amd.com for support.$\n$\nContinue installation?" IDYES install_gaia IDNO exit_installer
        ${elseif} $3 != ${NPU_DRIVER_VERSION}
          DetailPrint "- Current driver version ($3) is not the recommended version ${NPU_DRIVER_VERSION}"
          MessageBox MB_YESNO "WARNING: Current driver ($3) is not the recommended driver version ${NPU_DRIVER_VERSION}. If you run into issues, please install the recommended driver or reach out to gaia@amd.com for support.$\n$\nContinue installation?" IDYES install_gaia IDNO exit_installer
        ${Else}
          DetailPrint "- No driver update needed."
          GoTo install_gaia
        ${EndIf}
      ${EndIf}
      GoTo install_gaia

    update_driver:
      DetailPrint "- Installing Python requests library..."
      nsExec::ExecToLog '"$INSTDIR\gaia_env\python.exe" -m pip install requests'

      DetailPrint "- Downloading driver..."
      nsExec::ExecToLog '"$INSTDIR\gaia_env\python.exe" download_lfs_file.py ${RYZENAI_FOLDER}/${NPU_DRIVER_ZIP} $INSTDIR driver.zip ${OGA_TOKEN}'

      DetailPrint "- Updating driver..."
      nsExec::ExecToLog '"$INSTDIR\gaia_env\python.exe" npu_driver_utils.py --update-driver --folder_path $INSTDIR'

      RMDir /r "$INSTDIR\npu_driver_utils.py"
      GoTo install_gaia

    install_gaia:
      DetailPrint "---------------------"
      DetailPrint "- GAIA Installation -"
      DetailPrint "---------------------"

      DetailPrint "- Starting GAIA installation (this can take 5-10 minutes)..."
      DetailPrint "- See $INSTDIR\gaia_install.log for detailed progress..."
      ; Call the batch file with required parameters
      ExecWait '"$INSTDIR\install.bat" "$INSTDIR\gaia_env\python.exe" "$INSTDIR" $SELECTED_MODE' $R0

      ; Check if installation was successful
      ${If} $R0 == 0
        DetailPrint "*** INSTALLATION COMPLETED ***"
        DetailPrint "- GAIA package installation successful"

        ; Skip Ryzen AI WHL installation for GENERIC mode
        ${If} $SELECTED_MODE == "GENERIC"
          GoTo create_shortcuts
        ${Else}
          GoTo install_ryzenai_whl
        ${EndIf}
      ${Else}
        DetailPrint "*** INSTALLATION FAILED ***"
        DetailPrint "- Please check $INSTDIR\gaia_install.log for detailed error information"
        DetailPrint "- For additional support, please contact gaia@amd.com and"
        DetailPrint "include the log file, or create an issue at"
        DetailPrint "https://github.com/amd/gaia"
        ${IfNot} ${Silent}
          MessageBox MB_OK "GAIA installation failed.$\n$\nPlease check $INSTDIR\gaia_install.log for detailed error information."
        ${EndIf}
        Abort
      ${EndIf}

    install_ryzenai_whl:
      DetailPrint "-----------------------------"
      DetailPrint "- Ryzen AI WHL Installation -"
      DetailPrint "-----------------------------"

      ; Install OGA NPU dependencies
      DetailPrint "- Installing $SELECTED_MODE dependencies..."
      ${If} $SELECTED_MODE == "NPU"
        nsExec::ExecToLog 'conda run -p "$INSTDIR\gaia_env" lemonade-install --ryzenai npu -y --token ${OGA_TOKEN}'
        Pop $R0  ; Return value
        ${If} $R0 != 0
          DetailPrint "*** ERROR: NPU dependencies installation failed ***"
          DetailPrint "- Please review the output above to diagnose the issue."
          DetailPrint "- You can save this window's content by right-clicking and"
          DetailPrint "selecting 'Copy Details To Clipboard'"
          DetailPrint "- For additional support, please contact gaia@amd.com and"
          DetailPrint "include the log file, or create an issue at"
          DetailPrint "https://github.com/amd/gaia"
          DetailPrint "- When ready, please close the window to exit the installer."
          ${IfNot} ${Silent}
            MessageBox MB_OK "Failed to install NPU dependencies. Please review the installer output window for details by clicking on 'Show details'."
          ${EndIf}
          Abort
        ${EndIf}
      ${ElseIf} $SELECTED_MODE == "HYBRID"
        DetailPrint "- Running lemonade-install for hybrid mode..."
        nsExec::ExecToLog 'conda run -p "$INSTDIR\gaia_env" lemonade-install --ryzenai hybrid -y'
        Pop $R0  ; Return value
        ${If} $R0 != 0
          DetailPrint "*** ERROR: Hybrid dependencies installation failed ***"
          DetailPrint "- Please review the output above to diagnose the issue."
          DetailPrint "- You can save this window's content by right-clicking and"
          DetailPrint "selecting 'Copy Details To Clipboard'"
          DetailPrint "- For additional support, please contact gaia@amd.com and"
          DetailPrint "include the log output details."
          DetailPrint "- When ready, please close the window to exit the installer."
          ${IfNot} ${Silent}
            MessageBox MB_OK "Failed to install Hybrid dependencies. Please review the installer output window for details by clicking on 'Show details'."
          ${EndIf}
          Abort
        ${EndIf}
      ${EndIf}

      DetailPrint "- Dependencies installation completed successfully"
      GoTo update_settings

    update_settings:
      ${If} $SELECTED_MODE == "NPU"
        DetailPrint "- Copying NPU-specific settings"
        CopyFiles "$INSTDIR\src\gaia\interface\npu_settings.json" "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\npu_settings.json"

      ${ElseIf} $SELECTED_MODE == "HYBRID"
        DetailPrint "- Copying Hybrid-specific settings"
        CopyFiles "$INSTDIR\src\gaia\interface\hybrid_settings.json" "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\hybrid_settings.json"

      ${ElseIf} $SELECTED_MODE == "GENERIC"
        DetailPrint "- Copying Generic-specific settings"
        CopyFiles "$INSTDIR\src\gaia\interface\generic_settings.json" "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\generic_settings.json"
      ${EndIf}
      GoTo run_raux_installer

    run_raux_installer:
      ; Check if user chose to install RAUX
      ${If} $InstallRAUX == "1"
        DetailPrint "---------------------"
        DetailPrint "- RAUX Installation -"
        DetailPrint "---------------------"

        DetailPrint "- Creating RAUX installation directory..."
        CreateDirectory "$LOCALAPPDATA\RAUX"

        DetailPrint "- Creating temporary directory for RAUX installation..."
        CreateDirectory "$LOCALAPPDATA\RAUX\raux_temp"
        SetOutPath "$LOCALAPPDATA\RAUX\raux_temp"

        DetailPrint "- Preparing for RAUX installation..."

        ; Copy the Python installer script to the temp directory
        File "${__FILE__}\..\raux_installer.py"

        DetailPrint "- Using Python script: $LOCALAPPDATA\RAUX\raux_temp\raux_installer.py"
        DetailPrint "- Installation directory: $LOCALAPPDATA\RAUX"
        DetailPrint "- Using system Python for the entire installation process"

        ; Execute the Python script with the required parameters using system Python
        ; Note: We're not passing the python-exe parameter, so it will use the system Python
        ExecWait 'python "$LOCALAPPDATA\RAUX\raux_temp\raux_installer.py" --install-dir "$LOCALAPPDATA\RAUX"' $R0

        DetailPrint "RAUX installation exit code: $R0"

        ; Check if installation was successful
        ${If} $R0 == 0
            DetailPrint "*** RAUX INSTALLATION COMPLETED ***"
            DetailPrint "- RAUX installation completed successfully"

            ; Get version from version.txt, default to "unknown" if not found
            StrCpy $5 "unknown"  ; Default version
            IfFileExists "$LOCALAPPDATA\RAUX\raux_temp\extracted_files\version.txt" 0 +4
                FileOpen $4 "$LOCALAPPDATA\RAUX\raux_temp\extracted_files\version.txt" r
                FileRead $4 $5
                FileClose $4
            DetailPrint "- RAUX Version: $5"

            ; Copy the launcher scripts to the RAUX installation directory
            DetailPrint "- Copying RAUX launcher scripts"
            File /nonfatal "/oname=$LOCALAPPDATA\RAUX\launch_raux.ps1" "${__FILE__}\..\launch_raux.ps1"
            File /nonfatal "/oname=$LOCALAPPDATA\RAUX\launch_raux.cmd" "${__FILE__}\..\launch_raux.cmd"

            ; Create shortcut to the batch wrapper script with version parameter
            CreateShortcut "$DESKTOP\RAUX.lnk" "$LOCALAPPDATA\RAUX\launch_raux.cmd" "--version $5 --mode $SELECTED_MODE" "$INSTDIR\src\gaia\interface\img\raux.ico"
        ${Else}
            DetailPrint "*** RAUX INSTALLATION FAILED ***"
            DetailPrint "- Please check the log file at $LOCALAPPDATA\GAIA\gaia_install.log"
            DetailPrint "- For additional support, please contact support@amd.com"
            ${IfNot} ${Silent}
                MessageBox MB_OK "RAUX installation failed.$\n$\nPlease check the log file at $LOCALAPPDATA\GAIA\gaia_install.log for detailed error information."
            ${EndIf}
            Abort
        ${EndIf}

        ; IMPORTANT: Do NOT attempt to clean up the temporary directory
        ; This is intentional to prevent file-in-use errors
        ; The directory will be left for the system to clean up later
        DetailPrint "- Intentionally NOT cleaning up temporary directory to prevent file-in-use errors"
        SetOutPath "$INSTDIR"
      ${Else}
        DetailPrint "- RAUX installation skipped by user choice"
      ${EndIf}

      ; Continue to shortcuts creation after RAUX installation (or skip)
      GoTo create_shortcuts

    create_shortcuts:
      DetailPrint "*** INSTALLATION COMPLETED ***"

      # Create shortcuts only in non-silent mode
      ${IfNot} ${Silent}
        CreateShortcut "$DESKTOP\GAIA-UI.lnk" "$SYSDIR\cmd.exe" "/C conda activate $INSTDIR\gaia_env > NUL 2>&1 && gaia" "$INSTDIR\src\gaia\interface\img\gaia.ico"
        CreateShortcut "$DESKTOP\GAIA-CLI.lnk" "$SYSDIR\cmd.exe" "/K conda activate $INSTDIR\gaia_env" "$INSTDIR\src\gaia\interface\img\gaia.ico"
      ${EndIf}

SectionEnd

Function RunGAIAUI
  ${IfNot} ${Silent}
    ExecShell "open" "$DESKTOP\GAIA-UI.lnk"
  ${EndIf}
FunctionEnd

Function RunGAIACLI
  ${IfNot} ${Silent}
    ExecShell "open" "$DESKTOP\GAIA-CLI.lnk"
  ${EndIf}
FunctionEnd

Function RunRAUX
  ${IfNot} ${Silent}
    ${If} $InstallRAUX == "1"
      IfFileExists "$DESKTOP\RAUX.lnk" 0 +2
        ExecShell "open" "$DESKTOP\RAUX.lnk"
    ${EndIf}
  ${EndIf}
FunctionEnd

