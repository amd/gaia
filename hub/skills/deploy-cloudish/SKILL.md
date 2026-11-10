---
name: deploy-cloudish
description: Deploy a web app, API, worker, or MCP server to cloudish.ai from a Dockerfile or an existing container image, and check its build, URL, logs, and runtime settings. Use when the user asks to deploy or host an app with Cloudish, or to manage an app already running there.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - network:write:cloudish.ai
    requirements:
      python: ">=3.10"
      dependencies:
        - requests>=2.32.3
    tools:
      - name: cloudish_create_key
        description: Create a Cloudish API key without an existing credential, returning the key and a claim URL.
        parameters:
          alias: {type: string, required: false, default: ""}
          agent: {type: string, required: false, default: gaia}
        returns: {type: object}
        atomic: true
      - name: cloudish_deploy
        description: Create or update a Cloudish project and deploy it from an image or a source folder.
        parameters:
          api_key: {type: string, required: true}
          name: {type: string, required: true}
          image: {type: string, required: false, default: ""}
          context_dir: {type: string, required: false, default: ""}
          port: {type: integer, required: false, default: 8080}
          env: {type: object, required: false, default: null}
          always_on: {type: boolean, required: false, default: false}
          sleep_after_minutes: {type: integer, required: false, default: 0}
          cpu_cores: {type: number, required: false, default: 0.0}
          memory_gb: {type: number, required: false, default: 0.0}
          volume_size_gb: {type: integer, required: false, default: 0}
          build_cpu_cores: {type: number, required: false, default: 0.0}
          build_memory_gb: {type: integer, required: false, default: 0}
        returns: {type: object}
        atomic: false
      - name: cloudish_build_status
        description: Check a Cloudish source build and read its logs.
        parameters:
          api_key: {type: string, required: true}
          build_id: {type: integer, required: true}
        returns: {type: object}
        atomic: true
      - name: cloudish_status
        description: Read a Cloudish project, including its URL, app state, resources, and credit rate.
        parameters:
          api_key: {type: string, required: true}
          owner: {type: string, required: true}
          name: {type: string, required: true}
        returns: {type: object}
        atomic: true
      - name: cloudish_logs
        description: Read the current container logs and Kubernetes events for a Cloudish app.
        parameters:
          api_key: {type: string, required: true}
          owner: {type: string, required: true}
          name: {type: string, required: true}
        returns: {type: object}
        atomic: true
      - name: cloudish_configure
        description: Change when a Cloudish app runs without rebuilding it.
        parameters:
          api_key: {type: string, required: true}
          owner: {type: string, required: true}
          name: {type: string, required: true}
          always_on: {type: boolean, required: false, default: null}
          sleep_after_minutes: {type: integer, required: false, default: null}
          wake_every_minutes: {type: integer, required: false, default: null}
        returns: {type: object}
        atomic: false
      - name: cloudish_set_secrets
        description: Set one or more encrypted environment secrets on a Cloudish project.
        parameters:
          api_key: {type: string, required: true}
          owner: {type: string, required: true}
          name: {type: string, required: true}
          secrets: {type: object, required: true}
        returns: {type: object}
        atomic: false
---

# Deploy on Cloudish

Cloudish is an agent-first container host: it builds from a Dockerfile on its
own machines, gives the app a public URL, and bills prepaid credits per second.
This skill wraps its HTTP API. No local Docker daemon is involved, and no
`docker build` should be run.

## Before you deploy

1. **Get a key.** Ask the user for a Cloudish API key if they have one. If they
   do not, call `cloudish_create_key` and show them the returned `claim_url`; the
   key works immediately, and claiming attaches it to their account for the
   dashboard and credit top-ups. Treat the key as a secret: keep it out of chat
   output, source files, and logs, and pass it only to the tools below.
2. **Find the real port.** The process's listening port, not the Dockerfile's
   `EXPOSE`. The default is 8080; nginx images usually listen on 80. A wrong
   port produces `503` with "nothing accepts connections on port …".
3. **Pick the source.** `image` deploys a prebuilt container image.
   `context_dir` builds from a folder that contains a `Dockerfile` at its root.
   Pass exactly one. The folder must be inside the agent's allowed folders; the
   tool skips hidden files and folders (so `.env` and `.git` stay out), plus
   `node_modules` and `__pycache__`. Check that nothing else secret is in it.
4. **Choose how it runs.** `always_on` is billed continuously. Otherwise the app
   sleeps after `sleep_after_minutes` without requests and wakes on the next
   one; 0 leaves Cloudish's default of 15 minutes. Ask the user — the cost
   difference is real.
5. **Plan the size.** 0 leaves Cloudish's default `shared` instance (billed for
   0.1 CPU and 0.2 GB, bursting to 1 CPU and 1 GB). Set `cpu_cores` and
   `memory_gb` for anything that must keep its memory, such as a database or a
   Next.js server.

## Procedure

1. **Create or update with `cloudish_deploy`.** The same call provisions a new
   project or redeploys an existing one with the same `name`. Put non-secret
   configuration in `env`; credentials go in `cloudish_set_secrets`.
2. **Source builds return a `build`.** Poll `cloudish_build_status` only while
   `build.status` is `"running"`; `"succeeded"`, `"failed"`, and a response with
   no status are terminal. During a long build, show only log lines the user has
   not seen.
3. **Report failures as they are.** Show `build.error` and the end of
   `build.logs`. If the only error is "Job has reached the specified backoff
   limit", the build probably ran out of resources rather than hitting a
   Dockerfile bug: retry with a higher `build_cpu_cores` (0.5, 1, 2, or 3) and
   `build_memory_gb` (1, 2, 4, or 5).
4. **Verify before claiming success.** A successful build registers the app, so
   call `cloudish_status` for `subdomain.url` and `cloudish_logs` to confirm the
   process started and is listening. Opening the URL is good evidence when the
   environment can reach it; inability to open it is not by itself a failure.
5. **Secrets and runtime changes are separate calls.** `cloudish_set_secrets`
   restarts a running app with the new values. `cloudish_configure` changes
   `always_on`, `sleep_after_minutes`, or `wake_every_minutes` without a rebuild.
6. **Close the loop with the user.** Report the project path (`owner/name`), the
   URL, whether the app is always-on or sleeping, its size and
   `creditsPerHour` from status, and anything left for them: claiming the key,
   DNS for a custom domain, or adding credit.

## Honest limits

- A key's balance is a hard ceiling. When it reaches zero, deploys return `402`
  and running containers stop within a minute; a volume and its data are kept.
  This skill cannot add credit — point the user at their dashboard.
- Container logs are fetched live and are not stored, so read them before
  redeploying if they matter.
- Deleting a project is not exposed here because it also deletes its volume.
  Use the Cloudish dashboard or `DELETE /api/v1/projects/{owner}/{name}`, and
  confirm with the user first.
- The build context is read through GAIA's allowed-folder check, so a folder
  outside them is refused before anything is uploaded.

## Fork this

The same shape works for any container host with a deploy API: keep the key an
explicit tool argument, keep destructive operations out, and make the procedure
verify the running app rather than trusting the create response.
