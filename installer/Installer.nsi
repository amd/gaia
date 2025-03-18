; Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
; GAIA Installer Script

; Define command line parameters
!define /ifndef MODE "GENERIC"  ; Default to GENERIC mode if not specified
!define /ifndef CI "OFF"  ; Default to OFF mode if not specified
!define /ifndef OGA_TOKEN ""  ; Default to empty string if not specified
!define /ifndef OGA_URL "https://api.github.com/repos/aigdat/ryzenai-sw-ea/contents/"
!define /ifndef RYZENAI_FOLDER "ryzen_ai_13_ga"
!define /ifndef NPU_DRIVER_ZIP "NPU_RAI1.3.zip"
!define /ifndef NPU_DRIVER_VERSION "32.0.203.240"

; Define main variables
Name "GAIA"

; This is a compile-time fix to make sure that our selfhost CI runner can successfully install,
; since LOCALAPPDATA points to C:\Windows for "system users"
!ifdef CI
  !if ${CI} == "ON"
    InstallDir "C:\Users\jfowe\AppData\Local\GAIA"
  !else
    InstallDir "$LOCALAPPDATA\GAIA"
  !endif
!else
  InstallDir "$LOCALAPPDATA\GAIA"
!endif

!ifdef MODE
  !if ${MODE} == "NPU"
    OutFile "GAIA_NPU_Installer.exe"
    !define MUI_WELCOMEFINISHPAGE_BITMAP ".\img\welcome_npu.bmp"
    !define /ifndef OGA_ZIP_FILE_NAME "oga-npu.zip"
    !define /ifndef OGA_WHEELS_PATH "amd_oga\wheels"
  !else if ${MODE} == "HYBRID"
    OutFile "GAIA_Hybrid_Installer.exe"
    !define MUI_WELCOMEFINISHPAGE_BITMAP ".\img\welcome_npu.bmp"
    !define /ifndef OGA_ZIP_FILE_NAME "oga-hybrid.zip"
    !define /ifndef OGA_WHEELS_PATH "hybrid-llm-artifacts_1.3.0\hybrid-llm-artifacts\onnxruntime_genai\wheel"
  !else
    ; By default, we will set to NPU artifacts
    OutFile "GAIA_Installer.exe"
    !define MUI_WELCOMEFINISHPAGE_BITMAP ".\img\welcome.bmp"
  !endif
!else
  ; By default, we will set to NPU artifacts
  OutFile "GAIA_Installer.exe"
  !define MUI_WELCOMEFINISHPAGE_BITMAP ".\img\welcome.bmp"
!endif

; Include modern UI elements
!include "MUI2.nsh"

; Enable StrLoc function
!include StrFunc.nsh
${StrLoc}

; Read version from version.py
!tempfile TMPFILE
!system 'python -c "with open(\"../src/gaia/version.py\") as f: exec(f.read()); print(version_with_hash)" > "${TMPFILE}"'
!define /file GAIA_VERSION "${TMPFILE}"
!delfile "${TMPFILE}"

; Define the GAIA_STRING variable
Var GAIA_STRING
Var GitPath

Function .onInit
  ${If} ${MODE} == "HYBRID"
    StrCpy $GAIA_STRING "GAIA - Ryzen AI Hybrid Mode, ver: ${GAIA_VERSION}"
  ${ElseIf} ${MODE} == "NPU"
    StrCpy $GAIA_STRING "GAIA - Ryzen AI NPU Mode, ver: ${GAIA_VERSION}"
  ${Else}
    StrCpy $GAIA_STRING "GAIA - Generic Mode, ver: ${GAIA_VERSION}"
  ${EndIf}
FunctionEnd

; Define constants
!define PRODUCT_NAME "GAIA"
!define GITHUB_REPO "https://github.com/aigdat/gaia.git"
!define EMPTY_FILE_NAME "empty_file.txt"
!define ICON_FILE "..\src\gaia\interface\img\gaia.ico"

; Finish Page settings
!define MUI_TEXT_FINISH_INFO_TITLE "GAIA installed successfully!"
!define MUI_TEXT_FINISH_INFO_TEXT "$GAIA_STRING has been installed successfully! A shortcut has been added to your Desktop. What would you like to do next?"

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION RunGAIAUI
!define MUI_FINISHPAGE_RUN_TEXT "Run GAIA UI"
!define MUI_FINISHPAGE_RUN_NOTCHECKED

!define MUI_FINISHPAGE_SHOWREADME
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION RunGAIACLI
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Run GAIA CLI"
!define MUI_FINISHPAGE_SHOWREADME_NOTCHECKED

; MUI Settings
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

; Set the installer icon
Icon ${ICON_FILE}

; Language settings
LangString MUI_TEXT_WELCOME_INFO_TITLE "${LANG_ENGLISH}" "Welcome to the GAIA Installer"
LangString MUI_TEXT_WELCOME_INFO_TEXT "${LANG_ENGLISH}" "This wizard will install $GAIA_STRING on your computer."
LangString MUI_TEXT_DIRECTORY_TITLE "${LANG_ENGLISH}" "Select Installation Directory"
LangString MUI_TEXT_INSTALLING_TITLE "${LANG_ENGLISH}" "Installing $GAIA_STRING"
LangString MUI_TEXT_FINISH_TITLE "${LANG_ENGLISH}" "Installation Complete"
LangString MUI_TEXT_FINISH_SUBTITLE "${LANG_ENGLISH}" "Thank you for installing GAIA!"
LangString MUI_TEXT_ABORT_TITLE "${LANG_ENGLISH}" "Installation Aborted"
LangString MUI_TEXT_ABORT_SUBTITLE "${LANG_ENGLISH}" "Installation has been aborted."
LangString MUI_BUTTONTEXT_FINISH "${LANG_ENGLISH}" "Finish"
LangString MUI_TEXT_LICENSE_TITLE ${LANG_ENGLISH} "License Agreement"
LangString MUI_TEXT_LICENSE_SUBTITLE ${LANG_ENGLISH} "Please review the license terms before installing GAIA."

; Define a section for the installation
Section "Install Main Components" SEC01
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
    DetailPrint '  Mode: ${MODE}'
    DetailPrint '  CI Mode: ${CI}'
    DetailPrint '  OGA URL: ${OGA_URL}'
    DetailPrint '  Ryzen AI Folder: ${RYZENAI_FOLDER}'
    DetailPrint '  Minimum NPU Driver Version: ${NPU_DRIVER_VERSION}'
    DetailPrint '-------------------------------------------'

    ; Pack GAIA into the installer
    ; Exclude hidden files (like .git, .gitignore) and the installation folder itself
    File /r /x installer /x .* /x ..\*.pyc ..\*.* npu_settings.json hybrid_settings.json generic_settings.json download_lfs_file.py npu_driver_utils.py amd_install_kipudrv.bat install.bat
    DetailPrint "- Packaged GAIA repo"

    ; Check if conda is available
    ExecWait 'where conda' $2
    DetailPrint "- Checked if conda is available"

    ; If conda is not found, show a message and exit
    ; Otherwise, continue with the installation
    StrCmp $2 "0" check_ollama conda_not_available

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
        GoTo check_ollama

      ${Else}
        DetailPrint "- Miniconda installation failed"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Error: Miniconda installation failed. Installation will be aborted."
        ${EndIf}
        GoTo exit_installer
      ${EndIf}

    check_ollama:
      DetailPrint "----------"
      DetailPrint "- Ollama -"
      DetailPrint "----------"

      ; Check if ollama is available only for GENERIC mode
      ${If} ${MODE} == "GENERIC"
        ExecWait 'where ollama' $2
        DetailPrint "- Checked if ollama is available"

        ; If ollama is not found, show a message and exit
        StrCmp $2 "0" create_env ollama_not_available
      ${Else}
        DetailPrint "- Skipping ollama check for ${MODE} mode"
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
      nsExec::ExecToLog '"$R1" init'

      DetailPrint "- Creating a Python 3.10 environment named 'gaia_env' in the installation directory: $INSTDIR..."
      ExecWait '"$R1" create -p "$INSTDIR\gaia_env" python=3.10 -y' $R0

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

      ${If} ${MODE} == "NPU"
      ${OrIf} ${MODE} == "HYBRID"
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
          MessageBox MB_YESNO "WARNING: Current driver could not be identified. Please install the minimum recommended driver version (${NPU_DRIVER_VERSION}) or reach out to gaia@amd.com for support.$\n$\nContinue installation?" IDYES install_gaia IDNO exit_installer
        ${elseif} $3 != ${NPU_DRIVER_VERSION}
          DetailPrint "- Current driver version ($3) is not the recommended version ${NPU_DRIVER_VERSION}"
          MessageBox MB_YESNO "WARNING: Current driver version ($3) is not the recommended version ${NPU_DRIVER_VERSION}. Please install the driver or reach out to gaia@amd.com for support.$\n$\nContinue installation?" IDYES install_gaia IDNO exit_installer
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
      ExecWait '"$INSTDIR\install.bat" "$INSTDIR\gaia_env\python.exe" "$INSTDIR" ${MODE}' $R0

      ; Check if installation was successful
      ${If} $R0 == 0
        DetailPrint "*** INSTALLATION COMPLETED ***"
        DetailPrint "- GAIA installation completed successfully"

        ; Skip Ryzen AI WHL installation for GENERIC mode
        ${If} ${MODE} == "GENERIC"
          GoTo update_settings
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
      DetailPrint "- Installing ${MODE} dependencies..."
      ${If} ${MODE} == "NPU"
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
      ${ElseIf} ${MODE} == "HYBRID"
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
      ${If} ${MODE} == "NPU"
        DetailPrint "- Replacing settings.json with NPU-specific settings"
        Delete "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\settings.json"
        Rename "$INSTDIR\npu_settings.json" "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\settings.json"

      ${ElseIf} ${MODE} == "HYBRID"
        DetailPrint "- Replacing settings.json with Hybrid-specific settings"
        Delete "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\settings.json"
        Rename "$INSTDIR\hybrid_settings.json" "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\settings.json"

      ${ElseIf} ${MODE} == "GENERIC"
        DetailPrint "- Replacing settings.json with Generic-specific settings"
        Delete "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\settings.json"
        Rename "$INSTDIR\generic_settings.json" "$INSTDIR\gaia_env\lib\site-packages\gaia\interface\settings.json"
      ${EndIf}

      DetailPrint "*** INSTALLATION COMPLETED ***"

      # Create shortcuts directly
      CreateShortcut "$DESKTOP\GAIA-UI.lnk" "$SYSDIR\cmd.exe" "/C conda activate $INSTDIR\gaia_env > NUL 2>&1 && gaia" "$INSTDIR\src\gaia\interface\img\gaia.ico"
      CreateShortcut "$DESKTOP\GAIA-CLI.lnk" "$SYSDIR\cmd.exe" "/K conda activate $INSTDIR\gaia_env" "$INSTDIR\src\gaia\interface\img\gaia.ico"

      Return
SectionEnd

Function RunGAIAUI
  ExecShell "open" "$DESKTOP\GAIA-UI.lnk"
FunctionEnd

Function RunGAIACLI
  ExecShell "open" "$DESKTOP\GAIA-CLI.lnk"
FunctionEnd

