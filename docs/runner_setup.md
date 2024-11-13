# NPU Runner Setup

This guide will help you set up a RyzenAI laptop as a GitHub self-host runner. That will make the laptop available for on-demand and CI jobs that require NPU resources.

1. IMPORTANT: before doing step 2, read this: when run `./config.cmd` in step 2, make the following choices:
    - Tell it `NT AUTHORITY\SYSTEM`, not the default of `NETWORK WHATEVER` (TODO FIXME the next time you see the dialog)
    - Use the group `stx`
    - Apply the label `stx`, as well as a label to indicate that you are maintaining the runner (for example, Jeremy puts the label `jefowers` on his runners)
1. Follow the instructions here for Windows, minding what we said in step 1: https://github.com/organizations/aigdat/settings/actions/runners/new
1. You should see your runner show up in the `stx` group here: https://github.com/organizations/aigdat/settings/actions/runner-groups/3