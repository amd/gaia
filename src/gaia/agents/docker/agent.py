#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Docker Agent for GAIA.

This agent provides an intelligent interface for containerizing applications,
generating Dockerfiles, and managing Docker containers through natural language commands.
"""
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict

from gaia.agents.base.console import AgentConsole, SilentConsole
from gaia.agents.base.mcp_agent import MCPAgent
from gaia.agents.base.tools import tool
from gaia.security import PathValidator

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen3-Coder-30B-A3B-Instruct-GGUF"
DEFAULT_MAX_STEPS = 10
DEFAULT_PORT = 8080


class DockerAgent(MCPAgent):
    """
    Intelligent Docker agent for containerization assistance.

    This agent helps developers containerize their applications by:
    - Analyzing application structure and dependencies
    - Generating appropriate Dockerfiles using LLM intelligence
    - Building Docker images
    - Running containers with proper configuration

    The agent uses Lemonade/LLM to understand the application context
    and generate optimal Dockerfiles based on best practices.
    """

    def __init__(self, **kwargs):
        """Initialize the Docker agent.

        Args:
            **kwargs: Agent initialization parameters:
                - max_steps: Maximum conversation steps (default: 10)
                - model_id: LLM model to use (default: Qwen3-Coder-30B-A3B-Instruct-GGUF)
                - silent_mode: Suppress console output (default: False)
                - debug: Enable debug logging (default: False)
                - show_prompts: Display prompts sent to LLM (default: False)
        """
        # Use larger coding model for reliable Dockerfile generation
        if "model_id" not in kwargs:
            kwargs["model_id"] = DEFAULT_MODEL

        if "max_steps" not in kwargs:
            kwargs["max_steps"] = DEFAULT_MAX_STEPS

        # Security: Configure allowed paths for file operations
        # If None, allow current directory and subdirectories
        self.allowed_paths = kwargs.pop("allowed_paths", None)
        self.path_validator = PathValidator(self.allowed_paths)

        super().__init__(**kwargs)

    def _get_system_prompt(self) -> str:
        """Generate the system prompt for Docker containerization.

        Returns:
            str: System prompt that teaches the LLM about Dockerfile best practices
        """
        return """You are a Docker containerization expert that responds ONLY in JSON format.

**CRITICAL RULES:**
1. Output ONLY valid JSON - nothing else
2. Do NOT add any text before the opening {
3. Do NOT add any text after the closing }
4. Your ENTIRE response must be parseable JSON

You help developers containerize their applications by:
- Analyzing application structure and dependencies
- Generating optimized Dockerfiles
- Building and running Docker containers

**Dockerfile Best Practices:**
- Use appropriate base images (python:3.9-slim for Python, node:18-alpine for Node.js)
- Minimize layers by combining RUN commands
- Copy dependency files first for better caching
- Use non-root users when possible
- Expose appropriate ports
- Set proper working directories

**Example Dockerfiles (use as inspiration - adapt to specific needs):**

Python/Flask application:
```
FROM python:3.9-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["python", "app.py"]
```

Node.js/Express application:
```
FROM node:18-alpine
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY . .
EXPOSE 3000
CMD ["npm", "start"]
```

**RESPONSE FORMAT - Use EXACTLY this structure:**

For ANALYZE (understand the app structure):
{"thought": "User wants to containerize X application", "goal": "Analyze application", "plan": [{"tool": "analyze_directory", "tool_args": {"path": "PATH_HERE"}}]}

For SAVE_DOCKERFILE (save generated Dockerfile):
{"thought": "Analyzed app, now generating Dockerfile content", "goal": "Save Dockerfile", "plan": [{"tool": "save_dockerfile", "tool_args": {"dockerfile_content": "FROM python:3.9-slim\\nWORKDIR /app\\n...", "path": ".", "port": 5000}}]}

For BUILD (build Docker image):
{"thought": "Building Docker image", "goal": "Build image", "plan": [{"tool": "build_image", "tool_args": {"path": "PATH", "tag": "TAG"}}]}

For RUN (run container):
{"thought": "Running container", "goal": "Run container", "plan": [{"tool": "run_container", "tool_args": {"image": "IMAGE", "port": "PORT_MAP"}}]}

For FINAL ANSWER:
{"thought": "Task completed", "goal": "Report results", "answer": "Successfully containerized the application. [Details about what was done]"}

**EXAMPLES:**

User: "create a Dockerfile for my Flask app"
Step 1: {"thought": "Need to analyze the app first", "goal": "Analyze application", "plan": [{"tool": "analyze_directory", "tool_args": {"path": "."}}]}
Step 2 (after seeing app_type=flask, entry_point=app.py): {"thought": "Flask app detected, I'll generate an appropriate Dockerfile", "goal": "Save Dockerfile", "plan": [{"tool": "save_dockerfile", "tool_args": {"dockerfile_content": "FROM python:3.9-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY . .\nEXPOSE 5000\nCMD [\"python\", \"app.py\"]", "path": ".", "port": 5000}}]}

User: "containerize and build my Node.js app"
Step 1: Analyze with analyze_directory
Step 2: Generate and save Node.js Dockerfile with save_dockerfile
Step 3: Build with build_image

**IMPORTANT:**
- First analyze the app to understand its structure
- Then generate appropriate Dockerfile content based on the analysis
- Include proper copyright header in Dockerfile
- Use save_dockerfile to write the Dockerfile you generated"""

    def _create_console(self):
        """Create console for Docker agent output."""
        if self.silent_mode:
            return SilentConsole()
        return AgentConsole()

    def _register_tools(self):
        """Register Docker-specific tools."""

        @tool
        def analyze_directory(path: str = ".") -> Dict[str, Any]:
            """Analyze application directory to determine type and dependencies.

            Args:
                path: Directory path to analyze (default: current directory)

            Returns:
                Dictionary containing application information:
                - app_type: Type of application (flask, node, python, etc.)
                - entry_point: Main application file
                - dependencies: Dependencies file (requirements.txt, package.json)
                - port: Suggested port to expose
                - additional_files: Other relevant files found
            """
            return self._analyze_directory(path)

        @tool
        def save_dockerfile(
            dockerfile_content: str, path: str = ".", port: int = 5000
        ) -> Dict[str, Any]:
            """Save a Dockerfile that you've generated.

            You should generate the Dockerfile content based on the application analysis
            and your knowledge of Docker best practices. Use the example Dockerfiles in
            the system prompt as guidance.

            Args:
                dockerfile_content: The complete Dockerfile content you've generated
                path: Directory where to save the Dockerfile (default: current directory)
                port: Port exposed by the application (default: 5000)

            Returns:
                Dictionary containing:
                - status: "success" or "error"
                - path: Path where Dockerfile was saved
                - next_steps: Instructions for building and running
            """
            return self._save_dockerfile(dockerfile_content, path, port)

        @tool
        def build_image(path: str = ".", tag: str = "app:latest") -> Dict[str, Any]:
            """Build Docker image from Dockerfile.

            Args:
                path: Directory containing Dockerfile
                tag: Image tag (default: app:latest)

            Returns:
                Dictionary containing:
                - success: Whether build succeeded
                - image: Image tag if successful
                - output: Build output
                - error: Error message if failed
            """
            return self._build_image(path, tag)

        @tool
        def run_container(
            image: str, port: str = None, name: str = None
        ) -> Dict[str, Any]:
            """Run Docker container from image.

            Args:
                image: Docker image to run
                port: Port mapping (e.g., "5000:5000")
                name: Container name (optional)

            Returns:
                Dictionary containing:
                - success: Whether container started
                - container_id: Container ID if successful
                - url: Access URL if port mapped
                - output: Run output
                - error: Error message if failed
            """
            return self._run_container(image, port, name)

    def _analyze_directory(self, path: str) -> Dict[str, Any]:
        """Analyze directory to determine application type and structure."""
        logger.debug(f"Analyzing directory: {path}")

        # Security check
        if not self.path_validator.is_path_allowed(path):
            return {
                "status": "error",
                "error": f"Access denied: {path} is not in allowed paths",
            }

        path_obj = Path(path).resolve()
        if not path_obj.exists():
            return {"status": "error", "error": f"Directory {path} does not exist"}

        result = {
            "path": str(path_obj),
            "app_type": "unknown",
            "entry_point": None,
            "dependencies": None,
            "port": DEFAULT_PORT,
            "additional_files": [],
        }

        # Check for Python/Flask application
        requirements_file = path_obj / "requirements.txt"
        if requirements_file.exists():
            result["app_type"] = "python"
            result["dependencies"] = "requirements.txt"

            # Read requirements to detect framework
            with open(requirements_file, "r", encoding="utf-8") as f:
                requirements = f.read().lower()
                if "flask" in requirements:
                    result["app_type"] = "flask"
                    result["port"] = DEFAULT_PORT
                elif "django" in requirements:
                    result["app_type"] = "django"
                    result["port"] = DEFAULT_PORT
                elif "fastapi" in requirements:
                    result["app_type"] = "fastapi"
                    result["port"] = DEFAULT_PORT

            # Find entry point
            for potential_entry in [
                "app.py",
                "main.py",
                "run.py",
                "server.py",
                "application.py",
            ]:
                if (path_obj / potential_entry).exists():
                    result["entry_point"] = potential_entry
                    break

        # Check for Node.js application
        package_json = path_obj / "package.json"
        if package_json.exists():
            result["app_type"] = "node"
            result["dependencies"] = "package.json"
            result["port"] = DEFAULT_PORT

            # Read package.json to understand the app better
            try:
                with open(package_json, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)

                # Check for start script
                if "scripts" in pkg_data and "start" in pkg_data["scripts"]:
                    result["start_command"] = pkg_data["scripts"]["start"]

                # Detect framework from dependencies
                deps = pkg_data.get("dependencies", {})
                if "express" in deps:
                    result["app_type"] = "express"
                elif "next" in deps:
                    result["app_type"] = "nextjs"
                elif "react" in deps:
                    result["app_type"] = "react"

                # Find entry point
                if "main" in pkg_data:
                    result["entry_point"] = pkg_data["main"]
                else:
                    for potential_entry in ["index.js", "server.js", "app.js"]:
                        if (path_obj / potential_entry).exists():
                            result["entry_point"] = potential_entry
                            break
            except Exception as e:
                logger.warning(f"Could not parse package.json: {e}")

        # Check for other important files
        for file_name in [
            ".env.example",
            "docker-compose.yml",
            "Dockerfile",
            ".dockerignore",
        ]:
            if (path_obj / file_name).exists():
                result["additional_files"].append(file_name)

        logger.debug(f"Analysis result: {result}")
        return result

    def _save_dockerfile(
        self, dockerfile_content: str, path: str, port: int
    ) -> Dict[str, Any]:
        """Save Dockerfile content generated by the LLM.

        Args:
            dockerfile_content: Dockerfile content generated by LLM
            path: Directory where to save the Dockerfile
            port: Port exposed by the application

        Returns:
            Dictionary with status, path, and next steps
        """
        logger.debug(f"Saving Dockerfile to: {path}")

        # Security check
        if not self.path_validator.is_path_allowed(path):
            return {
                "status": "error",
                "error": f"Access denied: {path} is not in allowed paths",
            }

        path_obj = Path(path).resolve()
        if not path_obj.exists():
            return {"status": "error", "error": f"Directory {path} does not exist"}

        dockerfile_path = path_obj / "Dockerfile"

        try:
            # Save the LLM-generated Dockerfile
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(dockerfile_content)

            # Generate image name from directory
            image_name = path_obj.name.lower().replace("_", "-").replace(" ", "-")

            return {
                "status": "success",
                "path": str(dockerfile_path),
                "next_steps": [
                    "1. Build the Docker image:",
                    f"   cd {path_obj}",
                    f"   docker build -t {image_name} .",
                    "",
                    "2. Run the container (keeps running in background):",
                    f"   docker run -d -p {port}:{port} --name {image_name}-container {image_name}",
                    "",
                    "3. Access your application at:",
                    f"   http://localhost:{port}",
                    "",
                    "4. View container logs:",
                    f"   docker logs -f {image_name}-container",
                    "",
                    "5. Stop the container when done:",
                    f"   docker stop {image_name}-container",
                ],
            }

        except Exception as e:
            return {"status": "error", "error": f"Failed to save Dockerfile: {str(e)}"}

    def _build_image(self, path: str, tag: str) -> Dict[str, Any]:
        """Build Docker image from Dockerfile."""
        logger.debug(f"Building Docker image: {tag} from {path}")

        # Security check
        if not self.path_validator.is_path_allowed(path):
            return {
                "status": "error",
                "error": f"Access denied: {path} is not in allowed paths",
            }

        # Check if Docker is available
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                return {
                    "status": "error",
                    "error": "Docker is not installed or not accessible. Please install Docker first.",
                }
        except Exception as e:
            return {"status": "error", "error": f"Cannot access Docker: {str(e)}"}

        # Build the image
        try:
            result = subprocess.run(
                ["docker", "build", "-t", tag, path],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for build
                check=False,
            )

            if result.returncode == 0:
                return {
                    "status": "success",
                    "success": True,
                    "image": tag,
                    "output": result.stdout,
                    "message": f"Successfully built Docker image: {tag}",
                }
            else:
                return {
                    "status": "error",
                    "success": False,
                    "error": f"Docker build failed: {result.stderr}",
                    "output": result.stdout,
                }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "error": "Docker build timed out after 5 minutes",
            }
        except Exception as e:
            return {"status": "error", "error": f"Failed to build image: {str(e)}"}

    def _run_container(
        self, image: str, port: str = None, name: str = None
    ) -> Dict[str, Any]:
        """Run Docker container from image."""
        logger.debug(f"Running container from image: {image}")

        # Build docker run command
        cmd = ["docker", "run", "-d"]  # Run in detached mode

        if port:
            cmd.extend(["-p", port])

        if name:
            cmd.extend(["--name", name])

        cmd.append(image)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30, check=False
            )

            if result.returncode == 0:
                container_id = result.stdout.strip()[:12]

                response = {
                    "status": "success",
                    "success": True,
                    "container_id": container_id,
                    "image": image,
                    "message": f"Container {container_id} is running",
                }

                if port:
                    host_port = port.split(":")[0]
                    response["url"] = f"http://localhost:{host_port}"
                    response[
                        "message"
                    ] += f"\nAccess your application at: http://localhost:{host_port}"

                return response
            else:
                return {
                    "status": "error",
                    "success": False,
                    "error": f"Failed to run container: {result.stderr}",
                    "output": result.stdout,
                }

        except subprocess.TimeoutExpired:
            return {"status": "error", "error": "Docker run command timed out"}
        except Exception as e:
            return {"status": "error", "error": f"Failed to run container: {str(e)}"}

    # MCP Interface Implementation
    def get_mcp_tool_definitions(self) -> list[Dict[str, Any]]:
        """Return MCP tool definitions for Docker agent."""
        return [
            {
                "name": "dockerize",
                "description": "Containerize an application by analyzing its structure, generating an optimized Dockerfile, building the Docker image, and running the container. Use this when the user wants to dockerize, containerize, or run their application in Docker. This performs the complete workflow: analyze → create Dockerfile → build image → run container.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "appPath": {
                            "type": "string",
                            "description": "Absolute path to the application's root directory. Must be a complete filesystem path (e.g., C:/Users/name/myapp on Windows or /home/user/myapp on Linux). This is where the Dockerfile will be created and where dependency files (requirements.txt, package.json) should exist.",
                        },
                        "port": {
                            "type": "integer",
                            "description": "The port that the application listens on inside the container. This will be exposed in the Dockerfile and mapped to the same host port when running the container. Common values: 5000 (Flask), 3000 (Node.js/Express), 8000 (Django), 8080 (general web apps). Default: 5000",
                            "default": 5000,
                        },
                    },
                    "required": ["appPath"],
                },
            }
        ]

    def execute_mcp_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute MCP tool call by delegating to LLM via process_query."""
        if tool_name == "dockerize":
            # Validate appPath is provided
            if "appPath" not in arguments:
                return {
                    "success": False,
                    "error": "appPath is required - must be an absolute path to the application directory",
                }

            app_path = arguments["appPath"]

            # Validate it's an absolute path
            path_obj = Path(app_path)
            if not path_obj.is_absolute():
                return {
                    "success": False,
                    "error": f"appPath must be an absolute path, got: {app_path}",
                }

            # Validate directory exists
            if not path_obj.exists():
                return {
                    "success": False,
                    "error": f"Directory does not exist: {app_path}",
                }

            # Validate it's a directory
            if not path_obj.is_dir():
                return {
                    "success": False,
                    "error": f"Path is not a directory: {app_path}",
                }

            # Security check
            if not self.path_validator.is_path_allowed(app_path):
                return {
                    "success": False,
                    "error": f"Access denied: {app_path} is not in allowed paths",
                }

            # Get parameters
            port = arguments.get("port", DEFAULT_PORT)

            # Construct natural language query for the LLM
            # Always do full workflow: dockerize → build → run
            query_parts = [f"Dockerize the application at {app_path}"]

            if port != DEFAULT_PORT:
                query_parts.append(f"using port {port}")

            query_parts.append("then build and run the container")

            query = " ".join(query_parts) + "."

            # Let the LLM orchestrate the workflow
            result = self.process_query(user_input=query, trace=False)

            # Extract the final result
            # process_query returns: {status: "success"/"failed"/"incomplete", result: ..., error_history: [...]}
            status = result.get("status", "incomplete")
            final_result = result.get("result", "")

            # Only report failure if status is explicitly "failed"
            # Intermediate errors/warnings are acceptable as long as the overall task succeeded
            if status == "success":
                return {
                    "success": True,
                    "status": "completed",
                    "result": final_result,
                    "steps_taken": result.get("steps_taken", 0),
                    "duration": result.get("duration", 0),
                }
            elif status == "failed":
                # Include error details only when actually failed
                error_history = result.get("error_history", [])
                error_msg = (
                    error_history[-1] if error_history else "Unknown error occurred"
                )
                return {
                    "success": False,
                    "error": error_msg,
                    "result": final_result,
                    "error_count": result.get("error_count", 0),
                }
            else:
                # Incomplete status
                return {
                    "success": False,
                    "error": "Task did not complete within step limit",
                    "result": final_result,
                    "steps_taken": result.get("steps_taken", 0),
                }

        else:
            raise ValueError(f"Unknown tool: {tool_name}")
