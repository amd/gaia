#### Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
#### SPDX-License-Identifier: MIT

# UI Development Guide

This guide explains how to modify and enhance the GAIA user interface using Qt Designer. Qt Designer is a visual design tool that allows you to create and edit graphical user interfaces without writing code directly.

![Qt Designer](../data/img/qt-designer.png)

## Prerequisites

1. A working GAIA installation (see [README](README.md))
2. Python environment with GAIA dependencies installed
3. UI development dependencies: `pip install -e PyQt6, pyqt6-tools`

## Using Qt Designer

### Launching Qt Designer
1. Navigate to your Python environment's Qt applications directory:
   ```bash
   C:\Users\<YOUR_USER>\miniconda3\envs\<YOUR_GAIA_ENV>\Lib\site-packages\qt6_applications\Qt\bin
   ```
2. Run `designer.exe`

### Editing the UI
1. In Qt Designer, open `src/gaia/interface/form.ui`
2. Make your desired changes to the interface
3. Save the file

### Working with Assets
If you need to add new assets (icons, images, etc.):
1. Place the new assets under `src/gaia/interface/img`
2. Update the resource file `src/gaia/interface/resource.qrc`
3. Make sure to reference the assets correctly in your UI design

## Compiling Changes

After making changes, you need to compile the updated files:

1. Navigate to the interface directory:
   ```bash
   cd src/gaia/interface
   ```

2. Compile the UI form:
   ```bash
   pyside6-uic form.ui -o ui_form.py
   ```

3. If you modified resources (optional):
   ```bash
   pyside6-rcc resource.qrc -o rc_resource.py
   ```

4. Fix the resource import in `ui_form.py`:
   - Find: `import resource_rc`
   - Replace with: `import gaia.interface.rc_resource as rc_resource`

5. Test your changes by running:
   ```bash
   gaia
   ```

## Troubleshooting

- If Qt Designer doesn't launch, verify your Python environment is activated
- If resources aren't showing up, ensure you've recompiled both the UI and resource files
- For import errors, check that the resource path replacement was done correctly
