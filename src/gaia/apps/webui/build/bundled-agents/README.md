# Bundled Agents — Build-Time Staging

This directory is the build-time staging area for agents that are preloaded in the
GAIA installer. Each subdirectory becomes an agent that is automatically seeded to
`~/.gaia/agents/<agent-id>/` on the user's first launch via `agent-seeder.cjs`.

The `zoo-agent/` here is a working example. To ship your own agent, replace it with
(or add alongside) a directory containing your `agent.py`. See the
[Custom Installer Playbook](https://amd-gaia.ai/playbooks/custom-installer) for the
full walkthrough.
