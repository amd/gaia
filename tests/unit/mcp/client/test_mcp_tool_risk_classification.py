# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for MCP tool risk classification.

MCP tool names are chosen by the connected server, so they can never appear in
GAIA's static ``TOOLS_REQUIRING_CONFIRMATION`` set. The classification instead
travels with the registry entry as ``requires_confirmation``, computed here.

The invariant under test is **fail closed**: a tool is exempt from the
confirmation gate only when the server proves it is read-only. Silence,
ambiguity, or a read-only claim contradicted by the tool's own name all mean
"confirm".
"""

import pytest

from gaia.mcp.client.mcp_client import MCPTool, mcp_tool_requires_confirmation


def _tool(name, annotations=None):
    return MCPTool(
        name=name,
        description=f"{name} description",
        input_schema={"type": "object", "properties": {}, "required": []},
        annotations=annotations if annotations is not None else {},
    )


class TestFailClosedDefaults:
    """Anything the server does not prove read-only requires confirmation."""

    @pytest.mark.parametrize(
        "annotations",
        [
            {},  # server sent no annotations at all
            {"destructiveHint": False},  # unrelated hints don't exempt
            {"readOnlyHint": False},
            {"readOnlyHint": None},
            {"readOnlyHint": "true"},  # string, not boolean — not proof
            {"readOnlyHint": 1},  # truthy but not True — not proof
        ],
        ids=[
            "no-annotations",
            "unrelated-hint",
            "explicitly-not-read-only",
            "null-hint",
            "string-hint",
            "truthy-non-bool-hint",
        ],
    )
    def test_unproven_read_only_requires_confirmation(self, annotations):
        assert mcp_tool_requires_confirmation("search_docs", annotations) is True

    def test_missing_annotations_dict_requires_confirmation(self):
        assert mcp_tool_requires_confirmation("search_docs", None) is True


class TestReadOnlyExemption:
    """A server that declares read-only for a read-shaped tool is believed."""

    @pytest.mark.parametrize(
        "name",
        ["get_current_time", "search", "list_repositories", "readFile", "describe_db"],
    )
    def test_declared_read_only_is_exempt(self, name):
        assert mcp_tool_requires_confirmation(name, {"readOnlyHint": True}) is False


class TestNameOverridesReadOnlyClaim:
    """A mutating verb in the name outranks a ``readOnlyHint: true`` claim.

    A server advertising ``delete_file`` as read-only is buggy or hostile;
    either way its annotation must not open the gate.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "write_file",
            "delete_file",
            "create_repository",
            "push_files",
            "execute_command",
            "move_file",
            "send_message",
            "run_query",
            "update_issue",
            "commit",
        ],
    )
    def test_snake_case_mutating_name_still_confirms(self, name):
        assert mcp_tool_requires_confirmation(name, {"readOnlyHint": True}) is True

    @pytest.mark.parametrize(
        "name",
        [
            "writeFile",
            "deleteBranch",
            "createOrUpdateFile",
            "executeCommand",
            "WRITEFile",  # screaming camel — must not collapse to one token
            "POSTMessage",
        ],
    )
    def test_camel_case_mutating_name_still_confirms(self, name):
        """Token splitting must handle camelCase — many MCP servers use it."""
        assert mcp_tool_requires_confirmation(name, {"readOnlyHint": True}) is True

    @pytest.mark.parametrize(
        "name",
        [
            # desktop-commander renamed execute_command to these — the exact
            # RCE tools named in the vulnerability report.
            "start_process",
            "interact_with_process",
            # github
            "fork_repository",
            "approve_pull_request",
            "submit_pending_pull_request_review",
            "assign_copilot_to_issue",
            "lock_issue",
            "transfer_issue",
            "mark_all_notifications_read",
            # git / filesystem
            "git_checkout",
            "clone_repository",
            "copy_file",
            "mkdir",
            "chmod",
            # slack
            "slack_post_message",
            "post_comment",
            # postgres
            "alter_table",
            "grant_privileges",
            "restore_backup",
            # containers / workflows
            "restart_container",
            "stop_container",
            "launch_app",
            "trigger_workflow",
            # misc real-world destructive verbs
            "disable_user",
            "provision_vm",
            "invite_user",
            "share_document",
            "cancel_order",
            "sign_transaction",
            "pay_invoice",
            "flush_cache",
            "clear_index",
            "sync_folder",
            "import_data",
            "archive_thread",
            "dismiss_notification",
        ],
    )
    def test_real_world_server_verbs_are_covered(self, name):
        """Regression for the false-negative surface: these are actual tool
        names shipped by widely-used MCP servers. A ``readOnlyHint: true`` on
        any of them must not open the gate."""
        assert mcp_tool_requires_confirmation(name, {"readOnlyHint": True}) is True

    def test_substring_match_does_not_false_positive(self):
        """``list_updates`` contains 'update' as a token and is treated as
        mutating; ``forward`` merely *contains* the letters of no verb token and
        must stay exempt. Tokenisation, not substring search."""
        assert (
            mcp_tool_requires_confirmation("forward", {"readOnlyHint": True}) is False
        )
        assert (
            mcp_tool_requires_confirmation("setting", {"readOnlyHint": True}) is False
        )


class TestRegistryEntry:
    """``to_gaia_format`` must stamp the flag onto the registry entry."""

    def test_unannotated_tool_entry_requires_confirmation(self):
        entry = _tool("write_file").to_gaia_format("filesystem")
        assert entry["name"] == "mcp_filesystem_write_file"
        assert entry["requires_confirmation"] is True

    def test_declared_read_only_tool_entry_is_exempt(self):
        entry = _tool("get_current_time", {"readOnlyHint": True}).to_gaia_format("time")
        assert entry["requires_confirmation"] is False

    def test_every_amd_catalogued_write_tool_is_gated(self):
        """The write/destructive tools of the MCP servers GAIA catalogues must
        all be gated even though none of them appears in any static set."""
        catalogued_writes = [
            "create_or_update_file",
            "push_files",
            "delete_file",
            "create_repository",
            "create_pull_request",
            "fork_repository",
            "write_file",
            "edit_file",
            "move_file",
            "create_directory",
            "execute_command",
            "edit_block",
        ]
        for name in catalogued_writes:
            entry = _tool(name).to_gaia_format("srv")
            assert entry["requires_confirmation"] is True, name


class TestListToolsCapturesAnnotations:
    """Annotations must survive the ``tools/list`` parse, or every tool would
    silently fall back to 'no annotations'."""

    def _client_with_tools(self, tools_payload):
        from unittest.mock import Mock

        from gaia.mcp.client.mcp_client import MCPClient

        client = MCPClient.__new__(MCPClient)
        client.name = "srv"
        client._tools = None
        client.transport = Mock()
        client.transport.send_request.return_value = {
            "result": {"tools": tools_payload}
        }
        return client

    def test_annotations_are_parsed(self):
        client = self._client_with_tools(
            [
                {
                    "name": "get_time",
                    "description": "d",
                    "inputSchema": {},
                    "annotations": {"readOnlyHint": True},
                }
            ]
        )
        assert client.list_tools()[0].annotations == {"readOnlyHint": True}

    @pytest.mark.parametrize("bad", ["readonly", ["readOnlyHint"], 1, None])
    def test_non_dict_annotations_degrade_to_gated_not_trusted(self, bad):
        client = self._client_with_tools(
            [
                {
                    "name": "get_time",
                    "description": "d",
                    "inputSchema": {},
                    "annotations": bad,
                }
            ]
        )
        tool = client.list_tools()[0]
        assert tool.annotations == {}
        assert tool.to_gaia_format("srv")["requires_confirmation"] is True
