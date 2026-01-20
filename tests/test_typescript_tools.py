# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Integration tests for TypeScript runtime tools."""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.code.tools.typescript_tools import TypeScriptToolsMixin


class TestTypeScriptTools:
    """Test suite for TypeScript runtime tools."""

    @pytest.fixture
    def typescript_mixin(self):
        """Create a TypeScriptToolsMixin instance and register tools."""
        mixin = TypeScriptToolsMixin()
        mixin.cache_dir = Path(tempfile.mkdtemp())
        # Register the tools
        mixin.register_typescript_tools()
        yield mixin
        # Cleanup registry
        _TOOL_REGISTRY.clear()

    @pytest.fixture
    def temp_project_dir(self):
        """Create a temporary project directory."""
        temp_dir = tempfile.mkdtemp()
        yield Path(temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_fetch_github_template(self, typescript_mixin, temp_project_dir):
        """Test fetching and caching GitHub templates."""
        template_url = "https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts"
        destination = temp_project_dir / "my-app"

        # Get tool from registry
        fetch_tool = _TOOL_REGISTRY["fetch_github_template"]["function"]
        result = fetch_tool(template_url=template_url, destination=str(destination))

        # Verify result structure
        assert result["success"] is True
        assert "destination" in result
        assert "cached" in result
        assert Path(result["destination"]).exists()

        # Verify template files exist
        assert (destination / "package.json").exists()
        assert (destination / "tsconfig.json").exists()

    def test_fetch_github_template_caching(self, typescript_mixin, temp_project_dir):
        """Test that templates are properly cached."""
        template_url = "https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts"
        dest1 = temp_project_dir / "app1"
        dest2 = temp_project_dir / "app2"

        fetch_tool = _TOOL_REGISTRY["fetch_github_template"]["function"]

        # First fetch
        result1 = fetch_tool(template_url=template_url, destination=str(dest1))
        assert result1["cached"] is False

        # Second fetch should use cache
        result2 = fetch_tool(template_url=template_url, destination=str(dest2))
        assert result2["cached"] is True
        assert result2["success"] is True

    def test_run_npm_command_install(self, typescript_mixin, temp_project_dir):
        """Test running npm install command."""
        # Create a minimal package.json
        package_json = {
            "name": "test-app",
            "version": "1.0.0",
            "dependencies": {"react": "^18.2.0"},
        }
        (temp_project_dir / "package.json").write_text(
            json.dumps(package_json, indent=2)
        )

        npm_tool = _TOOL_REGISTRY["run_npm_command"]["function"]
        result = npm_tool(command="install", working_dir=str(temp_project_dir))

        # Verify result structure
        assert result["success"] is True
        assert "output" in result
        assert "command" in result
        assert result["command"] == "npm install"

        # Verify node_modules was created
        assert (temp_project_dir / "node_modules").exists()

    def test_run_npm_command_build(self, typescript_mixin, temp_project_dir):
        """Test running npm build command."""
        # Setup a minimal Vite project structure
        package_json = {
            "name": "test-app",
            "version": "1.0.0",
            "scripts": {"build": "echo 'Build successful'"},
        }
        (temp_project_dir / "package.json").write_text(
            json.dumps(package_json, indent=2)
        )

        npm_tool = _TOOL_REGISTRY["run_npm_command"]["function"]
        result = npm_tool(
            command="run build",
            working_dir=str(temp_project_dir),
            package_manager="npm",
        )

        assert result["success"] is True
        assert "Build successful" in result["output"]

    def test_run_npm_command_with_yarn(self, typescript_mixin, temp_project_dir):
        """Test running commands with yarn package manager."""
        package_json = {"name": "test-app", "version": "1.0.0"}
        (temp_project_dir / "package.json").write_text(
            json.dumps(package_json, indent=2)
        )

        npm_tool = _TOOL_REGISTRY["run_npm_command"]["function"]
        result = npm_tool(
            command="install", working_dir=str(temp_project_dir), package_manager="yarn"
        )

        assert result["success"] is True or "yarn" in result.get("error", "")
        assert "yarn install" in result["command"]

    def test_validate_typescript_with_errors(self, typescript_mixin, temp_project_dir):
        """Test TypeScript validation on invalid code."""
        # Create tsconfig.json
        tsconfig = {
            "compilerOptions": {
                "target": "ES2020",
                "module": "ESNext",
                "jsx": "react-jsx",
                "strict": True,
            },
            "include": ["src"],
        }
        (temp_project_dir / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2))

        # Create INVALID TypeScript file (missing type)
        src_dir = temp_project_dir / "src"
        src_dir.mkdir()
        (src_dir / "App.tsx").write_text("""
import React from 'react';

const App = ({ name }) => {  // Missing type annotation
    return <div>Hello, {name}!</div>;
};

export default App;
""")

        package_json = {
            "name": "test-app",
            "dependencies": {
                "react": "^18.2.0",
                "@types/react": "^18.2.0",
                "typescript": "^5.0.0",
            },
        }
        (temp_project_dir / "package.json").write_text(
            json.dumps(package_json, indent=2)
        )

        subprocess.run(["npm", "install"], cwd=temp_project_dir, capture_output=True)

        validate_tool = _TOOL_REGISTRY["validate_typescript"]["function"]
        result = validate_tool(str(temp_project_dir))

        assert result["typescript_valid"] is False
        assert len(result["typescript_errors"]) > 0

    def test_install_dependencies(self, typescript_mixin, temp_project_dir):
        """Test dependency installation with type definitions."""
        package_json = {
            "name": "test-app",
            "version": "1.0.0",
            "dependencies": {"react": "^18.2.0", "axios": "^1.6.0"},
        }
        (temp_project_dir / "package.json").write_text(
            json.dumps(package_json, indent=2)
        )

        install_tool = _TOOL_REGISTRY["install_dependencies"]["function"]
        result = install_tool(project_path=str(temp_project_dir), package_manager="npm")

        assert result["success"] is True
        assert (temp_project_dir / "node_modules").exists()
        assert (temp_project_dir / "node_modules" / "react").exists()

        # Verify package.json was updated with type definitions
        updated_package = json.loads((temp_project_dir / "package.json").read_text())
        assert "@types/react" in updated_package.get(
            "devDependencies", {}
        ) or "@types/react" in updated_package.get("dependencies", {})

    def test_install_dependencies_with_pnpm(self, typescript_mixin, temp_project_dir):
        """Test dependency installation with pnpm."""
        package_json = {
            "name": "test-app",
            "version": "1.0.0",
            "dependencies": {"react": "^18.2.0"},
        }
        (temp_project_dir / "package.json").write_text(
            json.dumps(package_json, indent=2)
        )

        install_tool = _TOOL_REGISTRY["install_dependencies"]["function"]
        result = install_tool(
            project_path=str(temp_project_dir), package_manager="pnpm"
        )

        # Should succeed or gracefully handle pnpm not being installed
        assert "success" in result
        if result["success"]:
            assert (temp_project_dir / "node_modules").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
