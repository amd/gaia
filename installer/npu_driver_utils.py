# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import os
import shutil
import subprocess
import argparse
import zipfile


def unzip_file(zip_path, extract_to):
    """Unzips the specified zip file to the given directory."""
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_to)


def update_driver(folder_path):

    # Define the path to the zip file
    zip_file_path = os.path.join(folder_path, "driver.zip")

    # Unzip the file
    driver_path = os.path.join(folder_path, "driver")
    unzip_file(zip_file_path, driver_path)

    # Delete the zip file
    os.remove(zip_file_path)

    # Move one level down
    driver_path = os.path.join(driver_path, "npu_mcdm_stack_prod")

    # Move custom installer to driver folder
    custom_installer = os.path.join(
        folder_path,
        "amd_install_kipudrv.bat",
    )
    shutil.copy(custom_installer, driver_path)

    # Launch installer with modified command to wait for key press before closing
    powershell_cmd = (
        "Start-Process powershell -Verb RunAs -ArgumentList '-NoExit', "
        f"'-Command', 'Set-Location -Path \"{driver_path}\"; "
        ".\\amd_install_kipudrv.bat; "
        'Write-Host "`nPress any key to close..."; '
        "$null = $Host.UI.RawUI.ReadKey(); "
        "Exit'"
    )
    subprocess.run(["powershell", "-Command", powershell_cmd], check=True)


def get_ipu_driver_version():
    try:
        # Run Get-PnpDevice command and decode the output
        command = (
            'powershell.exe -Command "& { '
            "$device = Get-PnpDevice | Where-Object { "
            '    $_.Class -eq \\"System\\" -and ($_.Description -like \\"*AMD IPU*\\") '
            '    -or ($_.Description -like \\"*NPU Compute Accelerator*\\") '
            "}; "
            "if ($device) { "
            "$driver = Get-PnpDeviceProperty "
            '-InstanceId $device.InstanceId -KeyName \\"DEVPKEY_Device_'
            'DriverVersion\\"; '
            'Write-Output (\\"Description: $($device.Description)`nVersion: $($driver.Data)\\" ) '
            '} else { Write-Output \\"Device not found.\\" } }"'
        )
        output = subprocess.check_output(command, shell=True, text=True).strip()

        # Split the output into lines and further split each line into its components
        # Example output:
        # Description     DriverVersion
        # AMD IPU Device  10.105.5.42
        lines = output.split("\n")
        if len(lines) > 1:
            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) > 1:
                    return parts[
                        -1
                    ]  # Return the last part of the line, which should be the driver version

    # Approach for collecting stats:
    # The philosophy here is to make stats collection non-blocking.
    # If stats can be gathered, that's great. If not, the code should
    # continue executing without interruption.
    # Hence we dont raise an assertion here, but instead return the exception
    # a string to be logged.
    except Exception as e:  # pylint: disable=broad-except
        return str(e)

    return "Driver not found"


if __name__ == "__main__":
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Update NPU Driver")
    parser.add_argument(
        "--folder_path", type=str, help="Install folder path", default=None
    )
    parser.add_argument(
        "--get-version",
        action="store_true",
        default=False,
        help="Get current driver version",
    )
    parser.add_argument(
        "--update-driver", action="store_true", default=False, help="Update driver"
    )
    # Parse the arguments
    args = parser.parse_args()
    folder_path = args.folder_path

    if args.get_version:
        print(get_ipu_driver_version(), end="")
    elif args.update_driver:
        update_driver(folder_path)
    else:
        print("No action specified")
