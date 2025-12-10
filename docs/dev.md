# GAIA Development Guide

**Table of Contents**
- [Introduction](#introduction)
- [Before You Start](#before-you-start)
- [Setup and Installation](#setup-and-installation)
- [Running GAIA](#running-gaia)
- [Troubleshooting](#troubleshooting)
- [Support](#support)

---

## Introduction

GAIA is an open-source framework that runs generative AI applications on AMD hardware. GAIA uses [ONNX Runtime GenAI](https://github.com/microsoft/onnxruntime-genai) via [Lemonade Server](https://lemonade-server.ai/) for running Large Language Models (LLMs).

GAIA utilizes both NPU and iGPU on Ryzen AI systems for optimal performance on 300 series processors or above.

---

## Before You Start

### System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **OS** | Windows 11 Pro 24H2 / Ubuntu 22.04+ | - |
| **RAM** | 16GB | 64GB |
| **CPU** | Ryzen AI 300-series | Ryzen AI MAX+ 395 |
| **Storage** | 20GB free | - |

**Driver Requirements:**
| Component | Minimum Version |
|-----------|-----------------|
| Radeon iGPU | `32.0.22029.1019` |
| NPU | `32.0.203.314` |

### Software Requirements

- **Git** - For version control
- **Lemonade Server** - Download from [lemonade-server.ai](https://lemonade-server.ai/)

---

## Setup and Installation

GAIA uses [uv](https://docs.astral.sh/uv/), a fast Python package manager that handles virtual environments, Python installation, and dependencies automatically.

### Step 1: Install uv

**Windows (PowerShell):**
```powershell
irm https://astral.sh/uv/install.ps1 | iex
```

**Linux/macOS:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Step 2: Clone GAIA

```bash
git clone https://github.com/amd/gaia.git
cd gaia
```

### Step 3: Create Virtual Environment

```bash
uv venv .venv --python 3.12
```

> **Note:** uv will automatically download Python 3.12 if not already installed.

### Step 4: Activate the Environment

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
.\.venv\Scripts\activate.bat
```

**Linux/macOS:**
```bash
source .venv/bin/activate
```

You should see `(.venv)` in your terminal prompt when activated.

### Step 5: Install Dependencies

```bash
uv pip install -e ".[dev]"
```

Or install with all extras:
```bash
uv pip install -e ".[dev,talk,rag]"
```

### Deactivating

When done working:
```bash
deactivate
```

> **See also:** [Chat SDK Documentation](./chat.md) | [Dependency Management Guide](./dependency-management.md)

---

## Running GAIA

### Verify Installation

```bash
gaia -v    # Check version
gaia -h    # View CLI options
```

### Start Chatting

```bash
# Single query
gaia chat -q "What is artificial intelligence?"

# Interactive session
gaia chat
```

> **Note:** GAIA automatically starts Lemonade Server when needed. For manual start: `lemonade-server serve`

### Running Electron Applications

GAIA includes Electron-based GUI applications. To run the JAX (Jira Agent Experience) app:

**Windows:**
```powershell
.\src\gaia\apps\jira\webui\run.ps1
```

**Linux/macOS:**
```bash
./src/gaia/apps/jira/webui/run.sh
```

---

## Troubleshooting

### "uv" command not found (Windows)

After installing uv, the command may not be recognized because PATH hasn't updated.

**Solution 1:** Restart PowerShell (simplest)

**Solution 2:** Update PATH in current session:
```powershell
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
```

**Solution 3:** Use full path:
```powershell
$env:USERPROFILE\.local\bin\uv.exe --version
```

### Virtual Environment Activation Fails (Windows)

If you see a script execution error:
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Model Loading Issues

1. Check available system memory
2. Verify model compatibility with your hardware
3. Ensure all dependencies are correctly installed

### Environment Variable Issues

1. Verify the virtual environment is activated (look for `(.venv)` prefix)
2. Try restarting your terminal
3. Re-run the activation command for your platform

---

## Support

Report issues to `gaia@amd.com` or create an [issue on GitHub](https://github.com/amd/gaia/issues).

---

## License

[MIT License](../LICENSE.md)

Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
