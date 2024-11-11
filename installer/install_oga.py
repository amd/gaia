import argparse
import os
import sys
import zipfile
import requests
import subprocess
import shutil


def unzip_file(zip_path, extract_to):
    """Unzips the specified zip file to the given directory."""
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_to)


def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description="Install OGA NPU")
    parser.add_argument("folder_path", type=str, help="Install folder path")

    # Parse the arguments
    args = parser.parse_args()
    folder_path = args.folder_path

    # Define the path to the zip file
    zip_file_path = os.path.join(folder_path, "oga-npu.zip")

    # Unzip the file
    unzip_file(zip_file_path, folder_path)

    # Install all whl files in the amd_oga folder
    wheels_path = os.path.join(folder_path, "amd_oga", "wheels")
    for file in os.listdir(wheels_path):
        if file.endswith(".whl"):
            print(f"Installing {file}")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    os.path.join(wheels_path, file),
                ]
            )

    # Delete the zip file
    os.remove(zip_file_path)

    # Move contents of oga_npu_path to lemonade_models_path
    oga_npu_path = os.path.join(folder_path, "amd_oga")
    lemonade_models_path = os.path.join(
        folder_path,
        "gaia_env",
        "Lib",
        "site-packages",
        "turnkeyml",
        "llm",
        "tools",
        "ort_genai",
        "models",
    )

    # Copy contents of amd_oga to lemonade's models folder
    for item in os.listdir(oga_npu_path):
        s = os.path.join(oga_npu_path, item)
        d = os.path.join(lemonade_models_path, item)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

    # Remove the original oga_npu_path
    shutil.rmtree(oga_npu_path)

    # Move DLLs from libs folder to models folder
    libs_path = os.path.join(lemonade_models_path, "libs")
    if os.path.exists(libs_path):
        for file in os.listdir(libs_path):
            if file.endswith(".dll"):
                src = os.path.join(libs_path, file)
                dst = os.path.join(lemonade_models_path, file)
                shutil.move(src, dst)
    else:
        raise Exception("Libs folder not found in the models directory.")


if __name__ == "__main__":
    main()
