# Bundled Agents — Build-Time Staging

This directory is the build-time staging area for agents that are preloaded in the
GAIA installer. Each subdirectory becomes an agent that is automatically seeded to
`~/.gaia/agents/<agent-id>/` on the user's first launch via `agent-seeder.cjs`.
Seeding happens at most once per machine — a marker at `~/.gaia/seeder/<agent-id>.seeded`
records it, so a user who deletes the agent is never re-seeded (delete the marker and
relaunch to get it back).

The stock GAIA installer bundles no agents, so this directory is empty apart from this
file. To ship your own agent, add a directory containing your `agent.py`. See the
[Custom Installer Playbook](https://amd-gaia.ai/docs/playbooks/custom-installer) for the
full walkthrough.
