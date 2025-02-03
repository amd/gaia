; Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
; GAIA Installer Script

; Define command line parameters
!define /ifndef MODE "GENERIC"  ; Default to GENERIC mode if not specified
!define /ifndef CI "OFF"  ; Default to OFF mode if not specified
!define /ifndef OGA_TOKEN ""  ; Default to empty string if not specified
!define /ifndef OGA_URL "https://api.github.com/repos/aigdat/ryzenai-sw-ea/contents/"
!define /ifndef RYZENAI_FOLDER "ryzen_ai_13_ga"
!define /ifndef NPU_DRIVER_ZIP "NPU_RAI1.3.zip"
!define /ifndef NPU_DRIVER_VERSION "32.0.203.237"

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

; Include LogicLib for logging in silent mode
!include LogicLib.nsh
Var LogHandle

; Define the GAIA_STRING variable
Var GAIA_STRING

Function .onInit
  ${If} ${MODE} == "HYBRID"
    StrCpy $GAIA_STRING "GAIA NPU/GPU Hybrid Mode"
  ${ElseIf} ${MODE} == "NPU"
    StrCpy $GAIA_STRING "GAIA NPU Mode"
  ${Else}
    StrCpy $GAIA_STRING "GAIA"
  ${EndIf}
FunctionEnd


; Define constants for better readability
!define GITHUB_REPO "https://github.com/aigdat/gaia.git"
!define EMPTY_FILE_NAME "empty_file.txt"
!define ICON_FILE "..\src\gaia\interface\img\gaia.ico"

; Finish Page settings
!define MUI_TEXT_FINISH_INFO_TITLE "$GAIA_STRING installed successfully!"
!define MUI_TEXT_FINISH_INFO_TEXT "A shortcut has been added to your Desktop. What would you like to do next?"

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION RunGAIA
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_RUN_TEXT "Run GAIA"

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
  FileWrite $0 "------------------------$\n"
  FileWrite $0 "- Installation Section -$\n"
  FileWrite $0 "------------------------$\n"

  ; Check if directory exists before proceeding
  IfFileExists "$INSTDIR\*.*" 0 continue_install
    ${IfNot} ${Silent}
      MessageBox MB_YESNO "An existing GAIA installation was found at $INSTDIR.$\nWould you like to remove it and continue with the installation?" IDYES remove_dir
      ; If user selects No, show exit message and quit the installer
      MessageBox MB_OK "Installation cancelled. Exiting installer..."
      Quit
    ${Else}
      Goto remove_dir
    ${EndIf}

  remove_dir:
    ; Try to remove directory and verify it was successful
    RMDir /r "$INSTDIR"
    FileWrite $0 "- Deleted all contents of install dir$\n"
    IfFileExists "$INSTDIR\*.*" 0 continue_install
      ${IfNot} ${Silent}
        MessageBox MB_OK "Unable to remove existing installation. Please close any applications using GAIA and try again."
      ${EndIf}
      Quit

  continue_install:
    ; Create fresh directory
    CreateDirectory "$INSTDIR"
    FileOpen $0 "$INSTDIR\install_log.txt" w
    FileWrite $0 "*** INSTALLATION STARTED ***$\n"

    ; Attatch console to installation to enable logging
    System::Call 'kernel32::GetStdHandle(i -11)i.r0'
    StrCpy $LogHandle $0 ; Save the handle to LogHandle variable
    System::Call 'kernel32::AttachConsole(i -1)i.r1'
    ${If} $LogHandle = 0
      ${OrIf} $1 = 0
      System::Call 'kernel32::AllocConsole()'
      System::Call 'kernel32::GetStdHandle(i -11)i.r0'
      StrCpy $LogHandle $0 ; Update the LogHandle variable if the console was allocated
    ${EndIf}
    FileWrite $0 "- Initialized logging$\n"

    ; Set the output path for future operations
    SetOutPath "$INSTDIR"

    FileWrite $0 "Starting '$GAIA_STRING' Installation...$\n"
    FileWrite $0 'Configuration:$\n'
    FileWrite $0 '  Install Dir: $INSTDIR$\n'
    FileWrite $0 '  Mode: ${MODE}$\n'
    FileWrite $0 '  CI Mode: ${CI}$\n'
    FileWrite $0 '  OGA URL: ${OGA_URL}$\n'
    FileWrite $0 '  Ryzen AI Folder: ${RYZENAI_FOLDER}$\n'
    FileWrite $0 '  Minimum NPU Driver Version: ${NPU_DRIVER_VERSION}$\n'
    FileWrite $0 '-------------------------------------------$\n'

    # Pack GAIA into the installer
    # Exclude hidden files (like .git, .gitignore) and the installation folder itself
    File /r /x installer /x .* /x ..\*.pyc ..\*.* npu_settings.json hybrid_settings.json generic_settings.json download_lfs_file.py npu_driver_utils.py amd_install_kipudrv.bat
    FileWrite $0 "- Packaged GAIA repo$\n"

    ; Check if conda is available
    ExecWait 'where conda' $2
    FileWrite $0 "- Checked if conda is available$\n"

    ; If conda is not found, show a message and exit
    ; Otherwise, continue with the installation
    StrCmp $2 "0" check_ollama conda_not_available

    conda_not_available:
      FileWrite $0 "- Conda not installed.$\n"
      ${IfNot} ${Silent}
        MessageBox MB_YESNO "Conda is not installed. Would you like to install Miniconda?" IDYES install_miniconda IDNO exit_installer
      ${Else}
        Goto install_miniconda
      ${EndIf}

    exit_installer:
      FileWrite $0 "- Something went wrong. Exiting installer$\n"
      Quit

    install_miniconda:
      FileWrite $0 "-------------$\n"
      FileWrite $0 "- Miniconda -$\n"
      FileWrite $0 "-------------$\n"
      FileWrite $0 "- Downloading Miniconda installer...$\n"
      ExecWait 'curl -s -o "$TEMP\Miniconda3-latest-Windows-x86_64.exe" "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"'

      ; Install Miniconda silently
      ExecWait '"$TEMP\Miniconda3-latest-Windows-x86_64.exe" /InstallationType=JustMe /AddToPath=1 /RegisterPython=0 /S /D=$PROFILE\miniconda3' $2
      ; Check if Miniconda installation was successful
      ${If} $2 == 0
        FileWrite $0 "- Miniconda installation successful$\n"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Miniconda has been successfully installed."
        ${EndIf}

        StrCpy $R1 "$PROFILE\miniconda3\Scripts\conda.exe"
        Goto check_ollama

      ${Else}
        FileWrite $0 "- Miniconda installation failed$\n"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Error: Miniconda installation failed. Installation will be aborted."
        ${EndIf}
        Goto exit_installer
      ${EndIf}

    check_ollama:
      FileWrite $0 "----------$\n"
      FileWrite $0 "- Ollama -$\n"
      FileWrite $0 "----------$\n"

      ; Check if ollama is available only for GENERIC mode
      ${If} ${MODE} == "GENERIC"
        ExecWait 'where ollama' $2
        FileWrite $0 "- Checked if ollama is available$\n"

        ; If ollama is not found, show a message and exit
        StrCmp $2 "0" create_env ollama_not_available
      ${Else}
        FileWrite $0 "- Skipping ollama check for ${MODE} mode$\n"
        Goto create_env
      ${EndIf}

    ollama_not_available:
      FileWrite $0 "- Ollama not installed.$\n"
      ${IfNot} ${Silent}
        MessageBox MB_OK "Ollama is not installed. Please install Ollama from ollama.com/download to proceed."
      ${EndIf}
      Goto create_env

    create_env:
      FileWrite $0 "---------------------$\n"
      FileWrite $0 "- Conda Environment -$\n"
      FileWrite $0 "---------------------$\n"

      FileWrite $0 "- Initializing conda...$\n"
      ; Use the appropriate conda executable
      ${If} $R1 == ""
        StrCpy $R1 "conda"
      ${EndIf}
      ; Initialize conda (needed for systems where conda was previously installed but not initialized)
      nsExec::ExecToStack '"$R1" init'

      FileWrite $0 "- Creating a Python 3.10 environment named 'gaia_env' in the installation directory: $INSTDIR...$\n"
      ExecWait '"$R1" create -p "$INSTDIR\gaia_env" python=3.10 -y' $R0

      ; Check if the environment creation was successful (exit code should be 0)
      StrCmp $R0 0 install_ffmpeg env_creation_failed

    env_creation_failed:
      FileWrite $0 "- ERROR: Environment creation failed$\n"
      ; Display an error message and exit
      ${IfNot} ${Silent}
        MessageBox MB_OK "ERROR: Failed to create the Python environment. Installation will be aborted."
      ${EndIf}
      Quit

    install_ffmpeg:
      FileWrite $0 "----------$\n"
      FileWrite $0 "- FFmpeg -$\n"
      FileWrite $0 "----------$\n"

      FileWrite $0 "- Installing FFmpeg using winget...$\n"
      nsExec::ExecToStack 'winget install ffmpeg'
      Pop $R0  ; Return value
      Pop $R1  ; Command output
      FileWrite $0 "- FFmpeg installation return code: $R0$\n"
      FileWrite $0 "- FFmpeg installation output:$\n$R1$\n"

      Goto install_ryzenai_driver

    install_ryzenai_driver:
      FileWrite $0 "--------------------------$\n"
      FileWrite $0 "- Ryzen AI Driver Update -$\n"
      FileWrite $0 "--------------------------$\n"

      ${If} ${MODE} == "NPU"
      ${OrIf} ${MODE} == "HYBRID"
        ; If in silent mode, skip driver update
        ${If} ${Silent}
          Goto install_gaia
        ${EndIf}

        FileWrite $0 "- Checking NPU driver version...$\n"
        nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" npu_driver_utils.py --get-version'
        Pop $2 ; Exit code
        Pop $3 ; Command output (driver version)
        FileWrite $0 "- Driver version: $3$\n"

        ; Check if the command was successful and a driver version was found
        ${If} $2 != "0"
        ${OrIf} $3 == ""
          FileWrite $0 "- Failed to retrieve current NPU driver version$\n"
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
          ; MessageBox MB_YESNO "Current driver version could not be identified. Would you like to update to ${NPU_DRIVER_VERSION} anyways?" IDYES update_driver IDNO install_gaia
          MessageBox MB_YESNO "WARNING: Current driver could not be identified. Please install the minimum recommended driver version (${NPU_DRIVER_VERSION}) or reach out to gaia@amd.com for support.\n\nContinue installation?" IDYES install_gaia IDNO exit_installer
        ${elseif} $3 < ${NPU_DRIVER_VERSION}
          FileWrite $0 "- Current driver version ($3) is not the recommended version ${NPU_DRIVER_VERSION}$\n"
          ; MessageBox MB_YESNO "Current driver version ($3) is not the recommended version ${NPU_DRIVER_VERSION}. Would you like to update it?" IDYES update_driver IDNO install_gaia
          MessageBox MB_YESNO "WARNING: Current driver version ($3) is not the minimum recommended version ${NPU_DRIVER_VERSION}. Please install the driver or reach out to gaia@amd.com for support.\n\nContinue installation?" IDYES install_gaia IDNO exit_installer
        ${Else}
          FileWrite $0 "- No driver update needed.$\n"
          GoTo install_gaia
        ${EndIf}
      ${EndIf}
      GoTo install_gaia

    update_driver:
      FileWrite $0 "- Installing Python requests library...$\n"
      nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" -m pip install requests'
      Pop $R0  ; Return value
      Pop $R1  ; Command output
      FileWrite $0 "- Pip install return code: $R0$\n"
      FileWrite $0 "- Pip install output:$\n$R1$\n"

      FileWrite $0 "- Downloading driver...$\n"
      nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" download_lfs_file.py ${RYZENAI_FOLDER}/${NPU_DRIVER_ZIP} $INSTDIR driver.zip ${OGA_TOKEN}'
      Pop $R0  ; Return value
      Pop $R1  ; Command output
      FileWrite $0 "- Download script return code: $R0$\n"
      FileWrite $0 "- Download script output:$\n$R1$\n"

      nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" npu_driver_utils.py --update-driver --folder_path $INSTDIR'
      Pop $R0  ; Return value
      Pop $R1  ; Command output
      FileWrite $0 "- Driver update return code: $R0$\n"
      FileWrite $0 "- Driver update output:$\n$R1$\n"

      RMDir /r "$INSTDIR\npu_driver_utils.py"
      GoTo install_gaia

    install_gaia:
      FileWrite $0 "--------------------$\n"
      FileWrite $0 "- GAIA Installation -$\n"
      FileWrite $0 "--------------------$\n"

      ${If} ${MODE} == "NPU"
        FileWrite $0 "- Installing GAIA NPU...$\n"
        nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" -m pip install -e "$INSTDIR"[npu,clip,joker] --no-warn-script-location' $R0
        Pop $R0  ; Return value
        Pop $R1  ; Command output
        FileWrite $0 "- GAIA install return code: $R0$\n"
        FileWrite $0 "- GAIA install output:$\n$R1$\n"
      ${ElseIf} ${MODE} == "HYBRID"
        FileWrite $0 "- Installing GAIA Hybrid...$\n"
        nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" -m pip install -e "$INSTDIR"[hybrid,clip,joker] --no-warn-script-location' $R0
        Pop $R0  ; Return value
        Pop $R1  ; Command output
        FileWrite $0 "- GAIA install return code: $R0$\n"
        FileWrite $0 "- GAIA install output:$\n$R1$\n"
      ${Else}
        FileWrite $0 "- Installing GAIA Generic...$\n"
        nsExec::ExecToStack '"$INSTDIR\gaia_env\python.exe" -m pip install -e "$INSTDIR"[dml,clip,joker] --no-warn-script-location' $R0
        Pop $R0  ; Return value
        Pop $R1  ; Command output
        FileWrite $0 "- GAIA install return code: $R0$\n"
        FileWrite $0 "- GAIA install output:$\n$R1$\n"
      ${EndIf}

      ; Check if gaia installatation was successful (exit code should be 0)
      StrCmp $R0 0 gaia_install_success gaia_install_failed

    gaia_install_success:
      FileWrite $0 "- GAIA installation successful$\n"

      ; Skip Ryzen AI WHL installation for GENERIC mode
      ${If} ${MODE} == "GENERIC"
        Goto update_settings
      ${Else}
        Goto install_ryzenai_whl
      ${EndIf}

    gaia_install_failed:
      FileWrite $0 "- GAIA installation failed$\n"
      ${IfNot} ${Silent}
        MessageBox MB_OK "ERROR: GAIA package failed to install using pip. Installation will be aborted."
      ${EndIf}
      Quit

    install_ryzenai_whl:
      FileWrite $0 "-----------------------------$\n"
      FileWrite $0 "- Ryzen AI WHL Installation -$\n"
      FileWrite $0 "-----------------------------$\n"

      ; Install OGA NPU dependencies
      FileWrite $0 "- Installing ${MODE} dependencies...$\n"
      ${If} ${MODE} == "NPU"
        nsExec::ExecToStack 'conda run -p "$INSTDIR\gaia_env" lemonade-install --ryzenai npu -y --token ${OGA_TOKEN}' $R0
      ${ElseIf} ${MODE} == "HYBRID"
        nsExec::ExecToStack 'conda run -p "$INSTDIR\gaia_env" lemonade-install --ryzenai hybrid -y' $R0
      ${EndIf}

      Pop $R0  ; Return value
      Pop $R1  ; Command output
      FileWrite $0 "- ${MODE} dependencies install return code: $R0$\n"
      FileWrite $0 "- ${MODE} dependencies install output:$\n$R1$\n"
      Goto update_settings

    update_settings:
      ${If} ${MODE} == "NPU"
        FileWrite $0 "- Replacing settings.json with NPU-specific settings$\n"
        Delete "$INSTDIR\src\gaia\interface\settings.json"
        Rename "$INSTDIR\npu_settings.json" "$INSTDIR\src\gaia\interface\settings.json"

      ${ElseIf} ${MODE} == "HYBRID"
        FileWrite $0 "- Replacing settings.json with Hybrid-specific settings$\n"
        Delete "$INSTDIR\src\gaia\interface\settings.json"
        Rename "$INSTDIR\hybrid_settings.json" "$INSTDIR\src\gaia\interface\settings.json"

      ${ElseIf} ${MODE} == "GENERIC"
        FileWrite $0 "- Replacing settings.json with Generic-specific settings$\n"
        Delete "$INSTDIR\src\gaia\interface\settings.json"
        Rename "$INSTDIR\generic_settings.json" "$INSTDIR\src\gaia\interface\settings.json"
      ${EndIf}

      FileWrite $0 "*** INSTALLATION COMPLETED ***$\n"
      # Create a shortcut inside $INSTDIR
      CreateShortcut "$INSTDIR\GAIA-UI.lnk" "$SYSDIR\cmd.exe" "/C conda activate $INSTDIR\gaia_env > NUL 2>&1 && gaia" "$INSTDIR\src\gaia\interface\img\gaia.ico"
      CreateShortcut "$INSTDIR\GAIA-CLI.lnk" "$SYSDIR\cmd.exe" "/K conda activate $INSTDIR\gaia_env" "$INSTDIR\src\gaia\interface\img\gaia.ico"

      # Create a desktop shortcut that points to the newly created shortcut in $INSTDIR
      CreateShortcut "$DESKTOP\GAIA-UI.lnk" "$INSTDIR\GAIA-UI.lnk"
      CreateShortcut "$DESKTOP\GAIA-CLI.lnk" "$INSTDIR\GAIA-CLI.lnk"

      Goto end

    end:
SectionEnd

Function RunGAIA
  ExecShell "open" "$INSTDIR\GAIA-UI.lnk"
FunctionEnd

