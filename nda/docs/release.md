# GAIA Release Process

This document describes the process for releasing updates to the open-source gaia repository [amd/gaia](https://github.com/amd/gaia)

1. There are three repositories we will be dealing with here:
    1. [gaia-pirate](https://github.com/aigdata/gaia): the private repository most development is happening.
    1. [gaia-internal](https://gitenterprise.xilinx.com/AIG-DAT/gaia_internal): the internal staging repository for legal scan.
    1. [gaia-public](https://github.com/amd/gaia): the public open-source repository.

## Introduction

`release.py` is a script designed to sync approved code from a private AMD repository (aigdat/gaia) to an open-source repository. Here's its main workflow:

1. **Input**: Takes a destination path (`-o` argument) where the OSS repo is cloned, and an optional `--internal` flag for AMD-internal syncs.

2. **File Filtering**:
   - Uses Git to get a list of tracked files in the source repository
   - Filters out files based on an exclude list (like NDA content, internal workflows, etc.)
   - The `--internal` flag can bypass these filters for AMD-internal use

3. **File Operations**:
   - Creates necessary directory structure in the target repository
   - Copies filtered files from source to destination
   - Updates GitHub links in markdown files
   - Prints detailed information about which files were copied and which were excluded

4. **Safety Features**:
   - Enforces the private repo as the single source of truth
   - Prevents accidental copying of internal/NDA content through the exclude list
   - Requires manual review of changes before committing to the OSS repo

The script is designed to be part of a controlled release process where changes must be reviewed before being pushed to the public repository.

## Release Process

### Step 1: Commit your changes to GAIA-Pirate

1. Edit any changes you need in [gaia-pirate](https://github.com/aigdata/gaia) in a separate branch and create a Pull Request into the `main` branch.
    1. We will assume for this example that `v0.7.4` is what you are targeting, change this value accordingly going forward.
    1. Make sure to edit [version.py](https://github.com/aigdat/gaia/blob/main/src/gaia/version.py), `__version__` variable with the correct value, typically iterate the minor number. For example:
        - `__version__ = "0.7.3"` would be changed to `__version__ = "0.7.4"`
    1. If you want to check in a file that should not be included in the OSS release, you have two options:
        - Add the file or folder path to the `exclude` list in `release.py`
        - Place the file or folder in the `./nda` directory, which is excluded by default from the release process

1. Review changes and complete the PR into the `main` branch of the [gaia-pirate](https://github.com/aigdata/gaia) repo.
1. Create a tag in the `main` branch that matches the version above and push it to origin.
    ```bash
    git checkout main
    git pull
    git tag v0.7.4
    git push --tags
    ```

### Step 2: Push your changes to GAIA-Internal

1. Clone the [gaia_internal](https://gitenterprise.xilinx.com/AIG-DAT/gaia_internal) repo in a separate folder and create a branch.
    ```bash
    git clone https://gitenterprise.xilinx.com/AIG-DAT/gaia_internal
    cd gaia_internal
    git branch v0.7.4
    git checkout v0.7.4
    ```
1. Run `python release.py -o <path-to-gaia-public>`
    1. For example: `python release.py -o ..\gaia_internal`
1. Create a Pull Request and initiate the code scan process. See [AMD Open Source Resources](https://amdcloud.sharepoint.com/sites/amd-legal/SitePages/OSRB.aspx) for more info.
1. Once the code has been approved for release, complete the PR into the main branch and continue to the next section.

### Step 3: Push your changes to GAIA-Public

1. Now that the changes have been scanned by legal, we can push them to the pubic repo.
1. Clone the [gaia-public](https://github.com/amd/gaia) repo in a separate folder and create a branch.
    ```bash
    git clone https://github.com/amd/gaia
    cd gaia
    git branch v0.7.4
    git checkout v0.7.4
    ```
1. Go to your gaia-internal clone and make sure you are on the commit that is targeting the release.
1. Run `python release.py -o <path-to-gaia-public>`
    1. For example: `python release.py -o ..\gaia_public\gaia`
1. Once all tests pass, complete the PR into the main branch.
1. Create a GitHub release in the public repository:
    1. Go to the [gaia-public](https://github.com/amd/gaia) repository on GitHub
    1. Click on "Releases" in the right sidebar
    1. Click "Create a new release"
    1. Choose the tag `v0.7.4` (or your version)
    1. Set the release title as "v0.7.4" (see other releases for examples)
    1. Add release notes describing the changes (for small updates, just use AI generated release notes)
    1. Click "Publish release"
