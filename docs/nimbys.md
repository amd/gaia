# 🌩️ NIMBYS 🌩️ Documentation

This page documents how to set up and maintain NIMBYS, a geo-distributed cloud of Ryzen AI hardware.

Topics:
 - [What is NIMBYS](#what-is-nimbys)
 - [NPU Runner Setup](#npu-runner-setup)
 - [Maintenance and Troubleshooting](#maintenance-and-troubleshooting)
    - [Check your runner's status](#check-your-runners-status)
    - [Actions are failing unexpectedly](#actions-are-failing-unexpectedly)
    - [Take a laptop off NIMBYS](#take-a-laptop-off-nimbys)
- [Creating Workflows](#creating-workflows)
    - [Capabilities and Limitations](#capabilities-and-limitations)
    - [Workflow Examples](#workflow-examples)

## What is 🌩️ NIMBYS 🌩️

NIMBYS is implemented as a pool of GitHub self-hosted runners. A "runner" is a computer that has installed GitHub's runner software, which runs a service that makes the laptop available to run GitHub Actions. In turn, Actions are defined by Workflows, which specify when the Action should run (manual trigger, CI, CD, etc.) and what the Action does (run tests, build packages, run an experiment, etc.). 

You can read about all this here: [GitHub: About self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners).

## NPU Runner Setup

This guide will help you set up a Ryzen AI laptop as a GitHub self-hosted runner as part of the NIMBYS cloud. This will make the laptop available for on-demand and CI jobs that require NPU resources.

### New Machine Setup

- If you are setting up a new computer from scratch, and don't want to attach a Microsoft account, follow these steps:
    - Press `shift+F10` during windows setup to open a terminal.
    - Run `oobe\BypassNRO` in the terminal. This will reboot the computer. Proceed with setup.
    - When you get to the “connect to network” screen, say you don’t have internet.
    - This will allow you to create an offline account for the computer.
- Install the following software:
    - The latest RyzenAI driver, which is currently [RyzenAI 1.3 GA](https://ryzenai.docs.amd.com/en/latest/inst.html#install-npu-drivers)
    - [VS Code](https://code.visualstudio.com/Download)
    - [git](https://git-scm.com/downloads/win)
    - [ollama](https://ollama.com/download)
- If your laptop has an Nvidia GPU, you must disable it in device manager
- Open a PowerShell script in admin mode, and run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned`
- Go into Windows settings:
  - Go to system, power & battery, screen sleep & hibernate timeouts, and make it so the laptop never sleeps while plugged in. If you don't do this it can fall asleep during jobs.
  - Searrch "Change the date and time", and then click "sync" under "additional settings."

### Runner Configuration

These steps will place your machine in the `stx-test` pool, which is where we put machines while we are setting them up. In the next section we will finalize setup and then move the runner into the production pool.

1. IMPORTANT: before doing step 2, read this: 
    - Use a powershell administrator mode terminal
    - Enable permissions by running `Set-ExecutionPolicy RemoteSigned`
    - When running `./config.cmd` in step 2, make the following choices:
         - Name of the runner group = `stx`
         - For the runner name, call it `NAME-stx-NUMBER`, where NAME is your alias and NUMBER would tell you this is the Nth STX machine you've added (e.g., `jefowers-stx-1` for Jeremy's first STX laptop)
         - Apply the label `stx-test`, as well as a label with your name to indicate that you are maintaining the runner (for example, Jeremy puts the label `jefowers` on his runners)
         - Accept the default for the work folder
         - You want the runner to function as a service (respond Y)
         - User account to use for the service = `NT AUTHORITY\SYSTEM` (not the default of `NT AUTHORITY\NETWORK SERVICE`)
    
1. Follow the instructions here for Windows, minding what we said in step 1: https://github.com/organizations/aigdat/settings/actions/runners/new
1. You should see your runner show up in the `stx` group here: https://github.com/organizations/aigdat/settings/actions/runner-groups/3

### Runner Setup

These steps will use GitHub Actions to run automated setup and validation for your new runner while it is still in the `stx-test` group.

1. Go to the [lemonade NPU test action](https://github.com/aigdat/genai/actions/workflows/test_npu.yml) and click "run workflow".
    - Select `stx-test` as the nimbys group
    - Check the box for "Install miniconda"
    - Click `Run workflow`
1. The workflow should appear at the top of the queue. Click to open it, then click into "make-npu-oga-lemonade".
    - Expand the `Set up job` section and make sure `Runner name:` refers to your new runner. Otherwise, the job may have gone to someone else's runner in the test group. You can re-queue the workflow until it lands on your runner.
    - Wait for the workflow to finish successfully.
1. In a powershell admin terminal, run `Stop-Service "actions.runner.*"` and then `Start-Service "actions.runner.*"`. If you don't do this, the runner wont be able to find Conda.
1. Repeat step 1, except do **not** check the box for "Install miniconda". Wait for it to finish successfully. Congrats, your new runner is working!
1. Go to the [Stx Runner Group](https://github.com/organizations/aigdat/settings/actions/runner-groups/3), click your new runner, and click the gear icon to change labels. Uncheck `stx-test` and check `stx`.
1. Done!

### Bonus

Make this picture your desktop wallpaper:

![image](https://github.com/user-attachments/assets/ac5c7744-1109-46a6-83e0-23b796ccfa98)

## Maintenance and Troubleshooting

NIMBYS is a production system and things will go wrong. Here is some advice on what to do.

### Check your runner's status

You can run `Get-EventLog -LogName Application -Source ActionsRunnerService` in a powershell terminal on your runner to get more information about what it's been up to.

If there have been any problems recently, they may show up like:

- Error: Runner connect error: < details about the connection error >
- Information: Runner reconnected
- Information: Running Job: < job name >
- Information: Jos < job name > completed with result: [Succeeded / Canceled / Failed]

### Actions are failing unexpectedly

Actions fail all the time, often because they are testing buggy code. However, sometimes an Action will fail because something is wrong with the specific NIMBYS runner that ran the Action. 

If this happens to you, here are some steps you can take (in order):
1. Take note of which runner executed your Action. You can check this by going to the `Set up job` section of the Action's log and checking the `Runner name:` field. The machine name in that field will correspond to a machine on the [runners page](https://github.com/organizations/aigdat/settings/actions/runners).
1. Re-queue your job. It is possible that that the failure is a one-off, and it will work the next time on the same runner. Re-queuing also gives you a chance of getting a runner that is in a healthier state.
1. If the same runner is consistently failing, it is probably in an unhealthy state (or you have a bug in your code and you're just blaming the runner). If a runner is in an unhealthy state:
    1. [Take the laptop off NIMBYS](#take-a-laptop-off-nimbys) so that it stops being allocated Actions.
    1. [Open an Issue](https://github.com/aigdat/gaia/issues/new/choose). Assign it to the maintainer of the laptop (their name should be in the runner's name). Link the multiple failed workflows that have convinced you that this runner is unhealthy.
    1. Re-queue your job. You'll definitely get a different runner now since you took the unhealthy runner offline.
1. If all runners are consistently failing your workflow, seriously think about whether your code is the problem.

### Take a laptop off NIMBYS

If you need to do some maintenance on your laptop, use it for dev/demo work, etc. you can remove it from the NIMBYS runners pool.

Also, if someone else's laptop is misbehaving and causing Actions to fail unexpectedly, you can remove that laptop from the runners pool to make sure that only healthy laptops are selected for work.

There are three options:

Option 1, which is available to anyone in the `aigdat` org: remove the `stx` label from the runner.
- NIMBYS workflows use `runs-on: stx` to target runners with the `stx` label. Removing this label from the runner will thus remove the runner from the pool.
- Go to the [runners page](https://github.com/organizations/aigdat/settings/actions/runners), click the specific runner in question, click the gear icon in the Labels section, and uncheck `stx`.
- To reverse this action later, go back to the [runners page](https://github.com/organizations/aigdat/settings/actions/runners), click the gear icon, and check `stx`.

Option 2, which requires physical/remote access to the laptop:
- In a PowerShell terminal, run `Stop-Service "actions.runner.*"`.
- To reverse this action, run `Start-Service "actions.runner.*"`.

Option 3 is to just turn the laptop off :)

## Creating Workflows

GitHub Workflows define the Actions that run on NIMBYS laptops to perform testing and experimentation tasks. This section will help you learn about what capabilities are available and show some examples of well-formed workflows.

### Capabilities and Limitations

Because NIMBYS uses self-hosted systems, we have to be careful about what we put into these workflows so that we avoid:
- Corrupting the laptops, causing them to produce inconsistent results or failures.
- Over-subscribing the capacity of the available laptops (3 at the time of this writing, scaling up to 12 in January 2025).

⚠️ NOTE: we could relieve some of these limitations by implementing container-based actions on our self-hosted runners. Anyone should feel free to try and make that work.

Here are some general guidelines to observe when creating or modifying NIMBYS workflows. If you aren't confident that you are properly following these guidelines, please contact someone to review your code before opening your PR.

- Place a NIMBYS emoji 🌩️ in the name of all of your NIMBYS workflows, so that PR reviewers can see at a glance which workflows are using NIMBYS resources.
    - Example: `name: Test Lemonade with DirectML 🌩️`
- Avoid triggering your workflow on NIMBYS before anyone has had a chance to review it against these guidelines. To avoid triggers, do not include `on: pull request:` in your workflow until after a reviewer has signed off.
- Only map a workflow to NIMBYS with `runs on: stx` if it actually requires Ryzen AI compute. If a step in your workflow can use generic compute (e.g., running a Hugging Face LLM on CPU), put that step on a generic non-NIMBYS runner like `runs on: windows-latest`.
- Be very considerate about installing software on to the runners:
    - Installing software into the CWD (e.g., a path of `.\`) is always ok, because that will end up in `C:\actions-runner\_work\REPO`, which is always wiped between tests.
    - Installing software into `AppData`, `Program Files`, etc. is not advisable because that software will persist across tests. See the [setup](#npu-runner-setup) section to see which software is already expected on the system.
        - ⚠️ NOTE: GAIA tests do install some software, see [Workflow Examples](#workflow-examples) for an example of why these specific cases are ok.
- Always create new conda environments in the CWD, for example `conda create -p .\my-env`.
    - This way, the conda environment is located in `C:\actions-runner\_work\REPO`, which is wiped between tests.
    - Do NOT create conda environments by name, for example `conda create -n dont-you-dare` since that will end up in the conda install location and will persist across tests.
    - Make sure to activate your conda environment (e.g., `conda activate .\lemon-npu-ci`) before running any `pip install` commands. Otherwise your workflow will modify the base environment!
- PowerShell scripts do not necessarily raise errors by programs they call.
    - That means PowerShell can call a Python test, and then keep going and claim "success" even if that Python test fails and raises an error (non-zero exit code).
    - You can add `if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }` after any line of script where it is that is particularly important to fail the workflow if the program in the preceding line raised an error.
        - For example, this will make sure that lemonade installed correctly: 
            1. pip install -e .[oga-npu]
            2. if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
- Be considerate of how long your workflow will run for, and how often it will be triggered.
    - All workflows go into the same queue and share the same pool of NIMBYS runners.
    - A good target length for a NIMBYS workflow is 15 minutes.
    - A single PR shouldn't kick off more than 2-4 NIMBYS workflow jobs.
    - Workflows that take hour(s) to run, or PRs that kick off dozens of jobs (e.g., with a test matrix) could easily fill up all NIMBYS runners, causing peoples' CI/CD and other workflows to wait for a long time. This will make people cranky with you.
- Be considerate of how much data your workflow will download.
    - It would be very bad to fill up a NIMBYS hard drive, since Windows machines misbehave pretty bad when their drives are full.
    - Place your Hugging Face cache inside the `_work` directory so that it will be wiped after each job.
        - Example: `$Env:HF_HOME=".\hf-cache"`
    - Place your Lemonade cache inside the `_work` directory so that it will be wiped after each job.
        - Example: `lemonade -d .\ci-cache` or `$Env:LEMONADE_CACHE_DIR=".\ci-cache"`. Use the environment variable, rather than the `-d` flag, wherever possible since it will apply to all lemonade calls within the job step.

### Workflow Examples

At the time of this writing, we have published 4 workflows for NIMBYS:
- [GAIA local npu tests](https://github.com/aigdat/gaia/blob/main/.github/workflows/local_npu_tests.yml)
    - Installs NSIS into `Program Files`. This is ok because NSIS is a quick install of a very mature software.
    - Installs GAIA into `AppData`. This is ok because the GAIA installer always deletes prior GAIA installs before attempting a new install. (Although it would still be preferable to install into `_work` if that was possible programmatically through an installer run-time option.)
- [Lemonade OGA NPU tests](https://github.com/aigdat/genai/blob/main/.github/workflows/test_npu.yml)
- [Lemonade OGA iGPU tests](https://github.com/aigdat/genai/blob/main/.github/workflows/test_dml.yml)
- [Lemonade on-demand OGA LLM evaluation](https://github.com/aigdat/genai/blob/main/.github/workflows/oga_nimbys_ondemand.yml)
