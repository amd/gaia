; GAIA Installer Script
Name "GAIA"
OutFile "GAIA_Installer.exe"
InstallDir "$LOCALAPPDATA\GAIA"

; Include modern UI elements
!include "MUI2.nsh"

; Include LogicLib for logging in silent mode
!include LogicLib.nsh
Var LogHandle

; Define constants for better readability
!define GITHUB_REPO "https://github.com/aigdat/gaia.git"
!define EMPTY_FILE_NAME "empty_file.txt"
!define ICON_FILE "..\src\gaia\interface\img\gaia.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP ".\img\welcome.bmp"

; Finish Page settings
!define MUI_TEXT_FINISH_INFO_TITLE "GAIA installed successfully!"
!define MUI_TEXT_FINISH_INFO_TEXT "A shortcut has been added to your Desktop. What would you like to do next?"

!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION RunGAIA
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!define MUI_FINISHPAGE_RUN_TEXT "Run GAIA"

; MUI Settings
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

; Set the installer icon
Icon ${ICON_FILE}

; Language settings
LangString MUI_TEXT_WELCOME_INFO_TITLE "${LANG_ENGLISH}" "Welcome to the GAIA Installer"
LangString MUI_TEXT_WELCOME_INFO_TEXT "${LANG_ENGLISH}" "This wizard will install GAIA on your computer."
LangString MUI_TEXT_DIRECTORY_TITLE "${LANG_ENGLISH}" "Select Installation Directory"
LangString MUI_TEXT_INSTALLING_TITLE "${LANG_ENGLISH}" "Installing GAIA"
LangString MUI_TEXT_FINISH_TITLE "${LANG_ENGLISH}" "Installation Complete"
LangString MUI_TEXT_FINISH_SUBTITLE "${LANG_ENGLISH}" "Thank you for installing GAIA!"
LangString MUI_TEXT_ABORT_TITLE "${LANG_ENGLISH}" "Installation Aborted"
LangString MUI_TEXT_ABORT_SUBTITLE "${LANG_ENGLISH}" "Installation has been aborted."
LangString MUI_BUTTONTEXT_FINISH "${LANG_ENGLISH}" "Finish"


; Define a section for the installation
Section "Install Main Components" SEC01

  ; Attatch console to installation to enable logging in silent mode (Github Workflow)
  ${If} ${Silent}
    System::Call 'kernel32::GetStdHandle(i -11)i.r0' 
    StrCpy $LogHandle $0 ; Save the handle to LogHandle variable
    System::Call 'kernel32::AttachConsole(i -1)i.r1' 
    ${If} $LogHandle = 0
      ${OrIf} $1 = 0
      System::Call 'kernel32::AllocConsole()'
      System::Call 'kernel32::GetStdHandle(i -11)i.r0'
      StrCpy $LogHandle $0 ; Update the LogHandle variable if the console was allocated
    ${EndIf}
  ${EndIf}
  FileWrite $0 "*** INSTALLATION STARTED ***$\n"

  ; Create the installation directory if it doesn't exist
  CreateDirectory "$INSTDIR"
  FileWrite $0 "- Install dir set$\n"

  ; Set the output path for future operations
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR"
  FileWrite $0 "- Deleted all contents of install dir$\n"

  # Pack GAIA into the installer
  # Exclude hidden files (like .git, .gitignore) and the installation folder itself
  File /r /x installer /x .* /x ..\*.pyc ..\*.*
  FileWrite $0 "- Packaged GAIA repo$\n"

  ; Check if conda is available
  ExecWait 'where conda' $2
  FileWrite $0 "- Checked if conda is available$\n"

  ; If conda is not found, show a message and exit
  StrCmp $2 "0" conda_available conda_not_available

  conda_not_available:
    FileWrite $0 "*** conda_not_available ***$\n"
    ${IfNot} ${Silent}
      MessageBox MB_OK "Conda is not installed. Please install Anaconda or Miniconda to proceed."
    ${EndIf}
    Quit ; Exit the installer after the message box is closed

  conda_available:
    FileWrite $0 "*** conda_available ***$\n"

    ; Check if ollama is available
    ExecWait 'where ollama' $2
    FileWrite $0 "- Checked if conda is available$\n"

    ; If ollama is not found, show a message and exit
    StrCmp $2 "0" ollama_available ollama_not_available

  ollama_not_available:
    FileWrite $0 "*** ollama_not_available ***$\n"
    ${IfNot} ${Silent}
      MessageBox MB_OK "Ollama is not installed. Please install Ollama from ollama.com/download to proceed."
    ${EndIf}
    Quit ; Exit the installer after the message box is closed

  ollama_available:
    FileWrite $0 "*** ollama_available ***$\n"
    ; Create a Python 3.10 environment named "gaia_env" in the installation directory
    ExecWait 'conda create -p "$INSTDIR\gaia_env" python=3.10 -y' $R0

    ; Check if the environment creation was successful (exit code should be 0)
    StrCmp $R0 0 env_created env_creation_failed
    
  env_creation_failed:
    FileWrite $0 "*** env_creation_failed ***$\n"
    ; Display an error message and exit
    ${IfNot} ${Silent}
      MessageBox MB_OK "Error: Failed to create the Python environment. Installation will be aborted."
    ${EndIf}
    Quit

  env_created:
    FileWrite $0 "*** env_created ***$\n"
    ; Install GAIA
    ExecWait '"$INSTDIR\gaia_env\python.exe" -m pip install -e "$INSTDIR"' $R0

    ; Check if gaia installatation was successful (exit code should be 0)
    StrCmp $R0 0 gaia_installed gaia_install_failed

  gaia_install_failed:
    FileWrite $0 "*** gaia_install_failed ***$\n"
    ${IfNot} ${Silent}
      MessageBox MB_OK "Error: GAIA package failed to install using pip. Installation will be aborted."
    ${EndIf}
    Quit

  gaia_installed:
    FileWrite $0 "*** gaia_installed ***$\n"
    # Create a shortcut inside $INSTDIR
    CreateShortcut "$INSTDIR\GAIA.lnk" "$SYSDIR\cmd.exe" "/C conda activate $INSTDIR\gaia_env && gaia" "$INSTDIR\src\gaia\interface\img\gaia.ico"

    # Create a desktop shortcut that points to the newly created shortcut in $INSTDIR
    CreateShortcut "$DESKTOP\GAIA.lnk" "$INSTDIR\GAIA.lnk"
    Goto end

  end:
SectionEnd

Function RunGAIA
  ExecShell "open" "$INSTDIR\GAIA.lnk"
FunctionEnd