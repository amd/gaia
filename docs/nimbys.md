# NIMBYS Documentation

This page documents how to set up and maintain NIMBYS, a geo-distributed cloud of RyzenAI hardware.

Topics:
 - [What is NIMBYS](#what-is-nimbys)
 - [NPU Runner Setup](#npu-runner-setup)
 - [Maintenance and Troubleshooting](#maintenance-and-troubleshooting)
    - [Check your runner's status](#check-your-runners-status)
    - [Actions are failing unexpectedly](#actions-are-failing-unexpectedly)
    - [Take a laptop off NIMBYS](#take-a-laptop-off-nimbys)

## What is NIMBYS

NIMBYS is implemented as a pool of GitHub self-hosted runners. A "runner" is a computer that has installed GitHub's runner software, which runs a service that makes the laptop available to run GitHub Actions. In turn, Actions are defined by Workflows, which specify when the Action should run (manual trigger, CI, CD, etc.) and what the Action does (run tests, build packages, run an experiment, etc.). 

You can read about all this here: [GitHub: About self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners/about-self-hosted-runners).

## NPU Runner Setup

This guide will help you set up a RyzenAI laptop as a GitHub self-hosted runner as part of the NIMBYS cloud. This will make the laptop available for on-demand and CI jobs that require NPU resources.

Pre-requisites:

- The laptop must already have the following software installed:
    - [git](https://git-scm.com/downloads/win)
    - [miniconda3](https://docs.anaconda.com/miniconda/)
        - After installing, you must run `conda init` or you will hit confusing errors later
    - [ollama](https://ollama.com/download)

Instructions:

1. IMPORTANT: before doing step 2, read this: 
    - Use a powershell administrator mode terminal
    - Enable permissions by running `Set-ExecutionPolicy RemoteSigned`
    - When running `./config.cmd` in step 2, make the following choices:
         - Name of the runner group = `stx`
         - For the runner name, call it `NAME-stx-NUMBER`, where NAME is your alias and NUMBER would tell you this is the Nth STX machine you've added (e.g., `jefowers-stx-1` for Jeremy's first STX laptop)
         - Apply the label `stx`, as well as a label with your name to indicate that you are maintaining the runner (for example, Jeremy puts the label `jefowers` on his runners)
         - Accept the default for the work folder
         - You want the runner to function as a service (respond Y)
         - User account to use for the service = `NT AUTHORITY\SYSTEM` (not the default of `NT AUTHORITY\NETWORK SERVICE`)
    
1. Follow the instructions here for Windows, minding what we said in step 1: https://github.com/organizations/aigdat/settings/actions/runners/new
1. You should see your runner show up in the `stx` group here: https://github.com/organizations/aigdat/settings/actions/runner-groups/3

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
