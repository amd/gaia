; Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
; GAIA Installer Script

; Define command line parameters
!define /ifndef RAUX_VERSION "v0.6.5+raux.0.2.4"
!define /ifndef RAUX_PRODUCT_NAME "GAIA UI"
!define /ifndef RAUX_PRODUCT_SQUIRREL_NAME "GaiaUi"
!define /ifndef RAUX_PRODUCT_SQUIRREL_PATH "$LOCALAPPDATA\GaiaUi"
!define /ifndef PYTHON_VERSION "3.10.9"
!define /ifndef PYTHON_EMBED_URL "https://www.python.org/ftp/python/${PYTHON_VERSION}/python-${PYTHON_VERSION}-embed-amd64.zip"
!define /ifndef GET_PIP_URL "https://bootstrap.pypa.io/get-pip.py"

; Request user rights only (no admin)
RequestExecutionLevel user

; Command line usage:
;  gaia-windows-setup.exe [/S] [/D=<installation directory>]
;    /S - Silent install with no user interface
;    /D=<path> - Set installation directory (must be last parameter)

; Define main variables
Name "GAIA"

InstallDir "$LOCALAPPDATA\GAIA"

; Include modern UI elements
!include "MUI2.nsh"

; Include LogicLib
!include LogicLib.nsh



; Include nsDialogs for custom pages
!include "nsDialogs.nsh"

; Enable StrLoc function
!include StrFunc.nsh
${StrLoc}

; Include WordFunc for version comparison
!include "WordFunc.nsh"
!insertmacro VersionCompare

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

; Variable to track whether to install RAUX
Var InstallRAUX

; Custom finish page variables
Var RunGAIAUICheckbox
Var RunGAIACheckbox
Var RunRAUXCheckbox

Function .onInit
  ; Set GAIA string
  StrCpy $GAIA_STRING "GAIA ${GAIA_VERSION}"

  ; Initialize InstallRAUX to 1 (checked)
  StrCpy $InstallRAUX "1"

  ; Hide RAUX option if not installed
  ${If} $InstallRAUX != "1"
    !define MUI_FINISHPAGE_SHOWREADME2 ""
  ${EndIf}
FunctionEnd

; Define constants
!define PRODUCT_NAME "GAIA"
!define ICON_FILE "../src/gaia/img/gaia.ico"

; Custom page for RAUX installation option
Function RAUXOptionsPage
  !insertmacro MUI_HEADER_TEXT "Additional Components" "Choose additional components to install"
  nsDialogs::Create 1018
  Pop $0

  ${NSD_CreateCheckbox} 10 10 100% 12u "Install ${RAUX_PRODUCT_NAME}"
  Pop $1
  ${NSD_SetState} $1 $InstallRAUX
  SetCtlColors $1 "" "transparent"

  ${NSD_CreateLabel} 25 30 100% 40u "${RAUX_PRODUCT_NAME} (an Open-WebUI fork) is AMD's new UI for interacting with AI models.$\nIt provides a chat interface similar to ChatGPT and other AI assistants."
  Pop $2
  SetCtlColors $2 "" "transparent"

  nsDialogs::Show
FunctionEnd

Function RAUXOptionsLeave
  ${NSD_GetState} $1 $InstallRAUX
FunctionEnd

; Custom finish page
Function CustomFinishPage
  nsDialogs::Create 1018
  Pop $R0

  ${NSD_CreateLabel} 0 20 100% 40u "$GAIA_STRING has been installed successfully! A shortcut has been added to your Desktop.$\n$\n$\nWhat would you like to do next?"
  Pop $0

  ${NSD_CreateCheckbox} 20 120 100% 12u "Run GAIA CLI"
  Pop $RunGAIACheckbox

  ${If} $InstallRAUX == "1"
    ${NSD_CreateCheckbox} 20 140 100% 12u "Run ${RAUX_PRODUCT_NAME}"
    Pop $RunRAUXCheckbox
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function CustomFinishLeave
  ${NSD_GetState} $RunGAIACheckbox $0
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
!insertmacro MUI_PAGE_DIRECTORY
Page custom RAUXOptionsPage RAUXOptionsLeave
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


; Define a section for the installation
Section "-Install Main Components" SEC01
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
    ; Remove existing installation directory
    RMDir /r "$INSTDIR"
    DetailPrint "- Deleted all contents of install dir"

    IfFileExists "$INSTDIR\*.*" 0 continue_install
      ${IfNot} ${Silent}
        MessageBox MB_OK "Unable to remove existing installation. Please close any applications using GAIA and try again."
      ${EndIf}
      DetailPrint "Failed to remove existing installation"
      Quit

  continue_install:

    ; Start RAUX installer early in parallel with GAIA setup
    Call run_raux_installer

    ; Create fresh directory
    CreateDirectory "$INSTDIR"
    CreateDirectory "$INSTDIR\python"

    ; Set the output path for future operations
    SetOutPath "$INSTDIR"

    DetailPrint "Starting '$GAIA_STRING' Installation..."
    DetailPrint 'Configuration:'
    DetailPrint '  Install Dir: $INSTDIR'
    DetailPrint '-------------------------------------------'

    ; Pack GAIA into the installer
    ; Exclude hidden files (like .git, .gitignore) and the installation folder itself
    File /r /x installer /x .* /x ..\*.pyc ..\*.* download_lfs_file.py install.bat launch_gaia.bat installer_utils.py
    DetailPrint "- Packaged GAIA repo"

    ; Create bin directory and move launch script there
    CreateDirectory "$INSTDIR\bin"
    Rename "$INSTDIR\launch_gaia.bat" "$INSTDIR\bin\launch_gaia.bat"
    DetailPrint "- Created bin directory and moved launch script"

    ; Download and set up embedded Python
    DetailPrint "-------------------"
    DetailPrint "- Python Setup -"
    DetailPrint "-------------------"
    DetailPrint "- Downloading embedded Python ${PYTHON_VERSION}..."

    ; Download embedded Python
    ExecWait 'curl -s -o "$INSTDIR\python\python.zip" "${PYTHON_EMBED_URL}"' $0
    ${If} $0 != 0
      DetailPrint "- ERROR: Failed to download Python"
      ${IfNot} ${Silent}
        MessageBox MB_OK "Failed to download Python. Installation will be aborted."
      ${EndIf}
      Quit
    ${EndIf}

    ; Extract Python zip
    DetailPrint "- Extracting Python..."
    nsExec::ExecToStack 'powershell -Command "Expand-Archive -Path \"$INSTDIR\python\python.zip\" -DestinationPath \"$INSTDIR\python\" -Force"'
    Pop $0
    Pop $1
    ${If} $0 != 0
      DetailPrint "- ERROR: Failed to extract Python"
      DetailPrint "- Error details: $1"
      ${IfNot} ${Silent}
        MessageBox MB_OK "Failed to extract Python. Installation will be aborted."
      ${EndIf}
      Quit
    ${EndIf}
    Delete "$INSTDIR\python\python.zip"

    ; Download get-pip.py
    DetailPrint "- Setting up pip..."
    ExecWait 'curl -sSL "${GET_PIP_URL}" -o "$INSTDIR\python\get-pip.py"' $0
    ${If} $0 != 0
      DetailPrint "- ERROR: Failed to download get-pip.py"
      ${IfNot} ${Silent}
        MessageBox MB_OK "Failed to download get-pip.py. Installation will be aborted."
      ${EndIf}
      Quit
    ${EndIf}

    ; Install pip
    ExecWait '"$INSTDIR\python\python.exe" "$INSTDIR\python\get-pip.py" --no-warn-script-location' $0
    ${If} $0 != 0
      DetailPrint "- ERROR: Failed to install pip"
      ${IfNot} ${Silent}
        MessageBox MB_OK "Failed to install pip. Installation will be aborted."
      ${EndIf}
      Quit
    ${EndIf}
    Delete "$INSTDIR\python\get-pip.py"

    ; Modify python*._pth file to include site-packages
    DetailPrint "- Configuring Python paths..."
    FileOpen $2 "$INSTDIR\python\python310._pth" a
    FileSeek $2 0 END
    FileWrite $2 "$\r$\nLib$\r$\n"
    FileWrite $2 "$\r$\nLib\site-packages$\r$\n"
    FileClose $2

    ; Check if Python setup was successful
    ExecWait '"$INSTDIR\python\python.exe" -c "print(\"Python test\")"' $0
    ${If} $0 != 0
      DetailPrint "- ERROR: Python setup failed"
      ${IfNot} ${Silent}
        MessageBox MB_OK "Failed to set up Python. Installation will be aborted."
      ${EndIf}
      Quit
    ${EndIf}

    ; Install required packaging module
    DetailPrint "- Installing packaging module..."
    nsExec::ExecToStack '"$INSTDIR\python\python.exe" -m pip install packaging'
    Pop $6  ; Return value
    Pop $7  ; Command output
    DetailPrint "- Packaging installation result:"
    DetailPrint "  Return code: $6"
    DetailPrint "  Output: $7"

    DetailPrint "- Python setup completed successfully"

    ; Continue with mode-specific setup
    GoTo check_lemonade

    check_lemonade:
      DetailPrint "------------"
      DetailPrint "- Lemonade -"
      DetailPrint "------------"

      ; First check if lemonade is available by trying to run it
      nsExec::ExecToStack 'cmd /c "lemonade-server --version 2>&1"'
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
        ; Lemonade is installed, now check version compatibility using Python script
        DetailPrint "- Lemonade is installed, checking version compatibility..."
        DetailPrint "- Expected version: ${LEMONADE_VERSION}"

        ; Call installer_utils.py to check version compatibility
        ; Python script returns exit code 0 if is_compatible=True, 1 if is_compatible=False
        DetailPrint "- Running version check command..."
        nsExec::ExecToStack 'cmd /c ""$INSTDIR\python\python.exe" "$INSTDIR\installer_utils.py" "${LEMONADE_VERSION}""'
        Pop $4  ; Return value (0 if compatible, 1 if not compatible)
        Pop $5  ; Command output

        DetailPrint "- Version check result:"
        DetailPrint "- Return code: $4"
        DetailPrint "- Output: $5"

        ; Use the is_compatible boolean value returned via exit code
        ${If} $4 == "0"
          DetailPrint "- Lemonade version is compatible (is_compatible=True)"
          GoTo create_env
        ${Else}
          DetailPrint "- Lemonade version is not compatible (is_compatible=False)"

          ; Extract the detected version from the Python script output
          ${StrLoc} $6 "$5" "VERSION:" ">"
          ${If} $6 != ""
            ; Calculate the position after "VERSION:" (8 characters)
            IntOp $6 $6 + 8
            StrCpy $7 "$5" "" $6  ; Extract everything after "VERSION:"

            ; Find the end of the line to get only the version part
            ${StrLoc} $8 "$7" "$\r" ">"
            ${If} $8 == ""
              ${StrLoc} $8 "$7" "$\n" ">"
            ${EndIf}
            ${If} $8 != ""
              StrCpy $7 "$7" $8 0  ; Get only the version part before newline
            ${EndIf}
          ${Else}
            StrCpy $7 "unknown"  ; Fallback if we can't parse the version
          ${EndIf}

          ${IfNot} ${Silent}
            MessageBox MB_YESNO "Your Lemonade version $7 is not compatible with the required version ${LEMONADE_VERSION}.$\n$\nWould you like to update Lemonade now?" IDYES install_lemonade IDNO skip_lemonade
          ${Else}
            GoTo skip_lemonade
          ${EndIf}
        ${EndIf}
      ${EndIf}

    install_lemonade:
      ; Check if file already exists and delete it first
      IfFileExists "$TEMP\Lemonade_Server_Installer.exe" 0 download_lemonade
        Delete "$TEMP\Lemonade_Server_Installer.exe"

      download_lemonade:
        DetailPrint "- Downloading Lemonade installer..."
        ; Use nsExec::ExecToStack to capture the output and error code
        nsExec::ExecToStack 'curl -L -f -v --retry 3 --retry-delay 2 -o "$TEMP\Lemonade_Server_Installer.exe" "https://github.com/lemonade-sdk/lemonade/releases/download/v${LEMONADE_VERSION}/Lemonade_Server_Installer.exe"'
        Pop $0  ; Return value
        Pop $1  ; Command output
        DetailPrint "- Curl return code: $0"
        DetailPrint "- Curl output: $1"

      ; Check if download was successful
      IfFileExists "$TEMP\Lemonade_Server_Installer.exe" lemonade_download_success lemonade_download_failed

      lemonade_download_failed:
        DetailPrint "- Failed to download Lemonade installer"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Failed to download Lemonade installer. Please install Lemonade manually from https://github.com/lemonade-sdk/lemonade/releases after installation completes."
        ${EndIf}
        GoTo skip_lemonade

      lemonade_download_success:
        DetailPrint "- Download successful ($TEMP\Lemonade_Server_Installer.exe), installing Lemonade..."

        ExecWait '"$TEMP\Lemonade_Server_Installer.exe" /Extras=hybrid' $2
        DetailPrint "- Lemonade installer finished with exit code: $2"

        ; Check if Lemonade installer succeeded based on exit code
        ${If} $2 == "0"
          DetailPrint "- Lemonade installation successful (exit code: $2)"
          ${IfNot} ${Silent}
            MessageBox MB_OK "Lemonade has been successfully installed."
          ${EndIf}
        ${Else}
          DetailPrint "- Lemonade installation failed (exit code: $2)"
          ${IfNot} ${Silent}
            MessageBox MB_OK "Lemonade installation failed with exit code $2. The installer may have been blocked by security software. Please install Lemonade manually from https://lemonade-server.ai and ensure it's not quarantined."
            Abort
          ${EndIf}
          ; Exit installation if Lemonade installer failed
          GoTo exit_installer
        ${EndIf}

        ; Clean up the downloaded installer
        Delete "$TEMP\Lemonade_Server_Installer.exe"
        GoTo create_env

    skip_lemonade:
      DetailPrint "- Continuing installation without Lemonade"
      GoTo create_env



    exit_installer:
      DetailPrint "- Installation cancelled. Exiting installer..."
      Quit

    create_env:
      DetailPrint "---------------------"
      DetailPrint "- Python Environment -"
      DetailPrint "---------------------"

      ; Install required packages
      DetailPrint "- Installing required Python packages..."
      nsExec::ExecToLog '"$INSTDIR\python\python.exe" -m pip install --upgrade pip setuptools wheel'
      Pop $R0
      ${If} $R0 != 0
        DetailPrint "- ERROR: Failed to install basic Python packages"
        ${IfNot} ${Silent}
          MessageBox MB_OK "ERROR: Failed to install required Python packages. Installation will be aborted."
        ${EndIf}
        Quit
      ${EndIf}

      # Download docopt.py TO FIX CIRCULAR DEPENDENCY ERROR
      DetailPrint "- Downloading docopt.py to fix circular dependency..."
      nsExec::ExecToStack 'curl -L -o "$INSTDIR\python\docopt.py" "https://raw.githubusercontent.com/docopt/docopt/master/docopt.py"'
      Pop $0  ; Return value
      Pop $1  ; Command output

      ${If} $0 != 0
        DetailPrint "- ERROR: Failed to download docopt.py"
        DetailPrint "- Error details: $1"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Failed to download docopt.py. Installation will be aborted."
        ${EndIf}
        Quit
      ${EndIf}

      ; Verify the file was downloaded successfully
      IfFileExists "$INSTDIR\python\docopt.py" 0 docopt_download_failed
        DetailPrint "- Successfully downloaded docopt.py"
        GoTo install_ffmpeg

      docopt_download_failed:
        DetailPrint "- ERROR: docopt.py file not found after download"
        ${IfNot} ${Silent}
          MessageBox MB_OK "Failed to verify docopt.py download. Installation will be aborted."
        ${EndIf}
        Quit

      GoTo install_ffmpeg

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
      GoTo install_gaia

    install_gaia:
      DetailPrint "---------------------"
      DetailPrint "- GAIA Installation -"
      DetailPrint "---------------------"

      DetailPrint "- Starting GAIA installation (this can take 5-10 minutes)..."
      DetailPrint "- See $INSTDIR\gaia_install.log for detailed progress..."
      ; Call the batch file with required parameters
      ExecWait '"$INSTDIR\install.bat" "$INSTDIR\python\python.exe" "$INSTDIR"' $R0

      ; Check if installation was successful
      ${If} $R0 == 0
        DetailPrint "*** INSTALLATION COMPLETED ***"
        DetailPrint "- GAIA package installation successful"

        ; Call RAUX installer after GAIA installation completes
        Call run_raux_installer
        
        ; Continue to create shortcuts
        GoTo create_shortcuts

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

    create_shortcuts:
      DetailPrint "*** INSTALLATION COMPLETED ***"

      DetailPrint "- Adding directories to user PATH..."

      ; Get the current user PATH from registry
      ReadRegStr $0 HKCU "Environment" "PATH"

      ; Prepare the new directories to add
      StrCpy $1 "$INSTDIR\bin"
      StrCpy $2 "$INSTDIR\python\Scripts"

      ; Check if directories are already in PATH
      ${StrLoc} $3 "$0" "$1" ">"
      ${StrLoc} $4 "$0" "$2" ">"

      ; Only proceed if at least one directory needs to be added
      ${If} $3 == ""
      ${OrIf} $4 == ""
        ; Add directories to user path - safely appending to PATH
        ${If} $0 == ""
          ; If PATH is empty, just set it to our directories
          WriteRegExpandStr HKCU "Environment" "PATH" "$1;$2"
          ${If} ${Errors}
            DetailPrint "- ERROR: Failed to write PATH to registry"
            ${IfNot} ${Silent}
              MessageBox MB_OKCANCEL "Failed to update PATH environment variable in the registry. Continue with installation?" IDOK continue_install_empty IDCANCEL abort_path_update_empty
              abort_path_update_empty:
                DetailPrint "- Installation aborted by user after PATH update failure"
                Abort "Installation aborted: Failed to update PATH environment variable."
              continue_install_empty:
                DetailPrint "- Continuing installation despite PATH update failure"
            ${EndIf}
          ${EndIf}
        ${Else}
          ; Otherwise append to the existing PATH with separators
          WriteRegExpandStr HKCU "Environment" "PATH" "$0;$1;$2"
          ${If} ${Errors}
            DetailPrint "- ERROR: Failed to write PATH to registry"
            ${IfNot} ${Silent}
              MessageBox MB_OKCANCEL "Failed to update PATH environment variable in the registry. Continue with installation?" IDOK continue_install_appended IDCANCEL abort_path_update_appended
              abort_path_update_appended:
                DetailPrint "- Installation aborted by user after PATH update failure"
                Abort "Installation aborted: Failed to update PATH environment variable."
              continue_install_appended:
                DetailPrint "- Continuing installation despite PATH update failure"
            ${EndIf}
          ${EndIf}
        ${EndIf}

        ; Notify Windows that environment variables have changed
        SendMessage ${HWND_BROADCAST} ${WM_WININICHANGE} 0 "STR:Environment" /TIMEOUT=5000
        ${If} ${Errors}
          DetailPrint "- WARNING: Failed to notify system of PATH change. Environment changes may require system restart."
        ${Else}
          DetailPrint "- Successfully updated user PATH with bin and Scripts directories"
        ${EndIf}
      ${Else}
        DetailPrint "- Directories already in PATH, no update needed"
      ${EndIf}

      # Create shortcuts only in non-silent mode
      ${IfNot} ${Silent}
        ; Create shortcuts using launch_gaia.bat
        CreateShortcut "$DESKTOP\GAIA CLI.lnk" "$INSTDIR\bin\launch_gaia.bat" "--cli" "$INSTDIR\src\gaia\img\gaia.ico"
      ${EndIf}
SectionEnd

Function RunGAIACLI
  ${IfNot} ${Silent}
    Exec '"$INSTDIR\bin\launch_gaia.bat" --cli'
  ${EndIf}
FunctionEnd

Function RunRAUX
  ${IfNot} ${Silent}
    ${If} $InstallRAUX == "1"
      IfFileExists "$DESKTOP\${RAUX_PRODUCT_NAME}.lnk" 0 +2
        ExecShell "open" "$DESKTOP\${RAUX_PRODUCT_NAME}.lnk"
    ${EndIf}
  ${EndIf}
FunctionEnd

; Place all functions here, outside of any Section or SectionGroup
Function run_raux_installer
  ; Check if user chose to install RAUX
  ${If} $InstallRAUX == "1"
    ; Check for existing RAUX install and uninstall if found
    Call check_raux_install
    ${If} $R7 != ""
      Call uninstall_raux
    ${Else}
      ; Fallback: check for Update.exe and uninstall
      StrCpy $R8 "$LOCALAPPDATA\${RAUX_PRODUCT_SQUIRREL_NAME}\\Update.exe"
      IfFileExists "$R8" 0 skip_uninstall_fallback
        Call uninstall_raux
      skip_uninstall_fallback:
    ${EndIf}

    ; Define local variable for RAUX_PREVENT_AUTOLAUNCH flag file path
    StrCpy $R9 "$TEMP\RAUX_PREVENT_AUTOLAUNCH"
    ; Define local variable for RAUX installer path
    StrCpy $R8 "$TEMP\raux-setup.exe"

    DetailPrint "[RAUX-Installer] ====================="
    DetailPrint "[RAUX-Installer] RAUX Installation"
    DetailPrint "[RAUX-Installer] ====================="

    ; Create RAUX_PREVENT_AUTOLAUNCH flag file
    DetailPrint "[RAUX-Installer] Creating auto-launch prevention flag file..."
    FileOpen $0 $R9 w
    FileWrite $0 "prevent"
    FileClose $0
    DetailPrint "[RAUX-Installer] Flag file created: $R9"

    ; Download RAUX installer
    DetailPrint "[RAUX-Installer] Downloading RAUX installer..."
    ExecWait 'curl -L -o "$R8" "https://github.com/aigdat/raux/releases/download/${RAUX_VERSION}/raux-setup.exe"' $0
    ${If} $0 != 0
      DetailPrint "[RAUX-Installer] ERROR: Failed to download RAUX installer"
      ${IfNot} ${Silent}
        MessageBox MB_OK "Failed to download RAUX installer. Continuing without RAUX."
      ${EndIf}
      DetailPrint "[RAUX-Installer] RAUX install failed, continuing with GAIA"
      Return
    ${EndIf}

    ; Start RAUX installer asynchronously (non-blocking)
    ${If} ${Silent}
      Exec '"$R8" /S'
    ${Else}
      Exec '"$R8"'
    ${EndIf}
  ${Else}
    DetailPrint "[RAUX-Installer] Installation skipped by user choice"
  ${EndIf}
  Return
FunctionEnd

; Function to check if RAUX is installed
Function check_raux_install
  ; Default to empty
  StrCpy $R7 ""
  ; Search HKCU uninstall registry for DisplayName == ${RAUX_PRODUCT_SQUIRREL_NAME}
  Push $0
  Push $1
  Push $2
  Push $3
  StrCpy $0 0
  loop_raux_reg:
    EnumRegKey $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall" $0
    StrCmp $1 "" end_raux_reg
    ReadRegStr $2 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\$1" "DisplayName"
    StrCmp $2 "${RAUX_PRODUCT_SQUIRREL_NAME}" 0 next_raux_reg
      ; Found matching DisplayName
      ReadRegStr $3 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\$1" "UninstallString"
      StrCpy $R7 $3
      Goto end_raux_reg
    next_raux_reg:
      IntOp $0 $0 + 1
      Goto loop_raux_reg
  end_raux_reg:
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Function to uninstall RAUX if installed
Function uninstall_raux
  ; If $R7 is set, use it; else fallback to Update.exe
  ${If} $R7 != ""
    DetailPrint "[RAUX-Installer] Uninstalling existing RAUX via registry uninstall string..."
    ExecWait '"$R7" /S'
  ${Else}
    ; Fallback: try Update.exe --uninstall
    StrCpy $R8 "$LOCALAPPDATA\${RAUX_PRODUCT_SQUIRREL_NAME}\\Update.exe"
    IfFileExists "$R8" 0 no_raux_uninstall
      DetailPrint "[RAUX-Installer] Uninstalling existing RAUX via Update.exe..."
      ExecWait '"$R8" --uninstall /S'
    no_raux_uninstall:
  ${EndIf}
FunctionEnd
