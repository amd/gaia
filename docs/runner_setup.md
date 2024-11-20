# NIMBYS - NPU Runner Setup

This guide will help you set up a RyzenAI laptop as a GitHub self-host runner as part of the NIMBYS cloud. That will make the laptop available for on-demand and CI jobs that require NPU resources.

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