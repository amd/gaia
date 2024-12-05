@REM Copyright(C) 2024 Advanced Micro Devices, Inc. All rights reserved.
@REM SPDX-License-Identifier: MIT

@echo off
set "ClassName=ComputeAccelerator"

echo Look for existing drivers to uninstall if needed
@echo off
for /f "tokens=2,* delims=: " %%a in ('pnputil /enum-drivers /class %ClassName% ^| findstr /i /r "oem[0-9]*.inf"') do (
    echo Deleting existing AMD IPU driver: %%b
    pnputil /delete-driver "%%b" /uninstall /f
)

echo Installing new driver
pnputil -i -a kipudrv.inf