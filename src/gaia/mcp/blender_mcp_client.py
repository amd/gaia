# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

# This Blender MCP client is a simplified and modified version of the BlenderMCP project from https://github.com/BlenderMCP/blender-mcp

import json
import logging
import re
import socket

from gaia.logger import get_logger


class MCPError(Exception):
    """Exception raised for MCP client errors."""


# MCP client class for tests
class MCPClient:
    log = get_logger(__name__)

    def __init__(self, host="localhost", port=9876):
        self.host = host
        self.port = port
        self.log = self.__class__.log  # Use the class-level logger for instances
        self.log.setLevel(logging.INFO)

    def _enhance_error_message(self, error_message):
        """Enhance error messages with more helpful information."""
        # Detect common Python errors and provide better context
        if "name '" in error_message and "is not defined" in error_message:
            # Extract variable name from NameError
            match = re.search(r"name '(\w+)' is not defined", error_message)
            if match:
                var_name = match.group(1)
                return f"Variable '{var_name}' is not defined. Make sure to declare it before use or check for typos."

        # Handle object not found errors
        if "Object not found:" in error_message:
            obj_name = error_message.replace("Object not found: ", "")
            return f"Object '{obj_name}' not found in the scene. It may have been deleted or renamed."

        # Return original message if no enhancement is available
        return error_message

    def send_command(self, cmd_type, params=None, timeout: float = 120.0):
        """Send a command to the Blender MCP server and return the parsed response.

        The Blender addon (src/gaia/mcp/blender_mcp_server.py) keeps the TCP
        connection open after responding so it can accept further commands on
        the same socket. We therefore cannot rely on ``recv()`` returning empty
        (FIN) to know the response is complete — the server will never send
        FIN. Instead we read incrementally and break as soon as a complete
        JSON document can be parsed, mirroring the server's own framing in
        ``blender_mcp_server.py:128-166``.

        ``timeout`` is the per-recv socket timeout (not cumulative) — long
        Blender operations like ``bpy.ops.render.render(...)`` can take many
        seconds without the server emitting any data, so the default is
        deliberately generous. Pass a higher value for very long renders or
        simulations.

        Regression-tested by ``tests/unit/mcp/test_blender_mcp_client.py``.
        Fixes issue #1022.
        """
        if params is None:
            params = {}

        # Create command
        command = {"type": cmd_type, "params": params}

        self.log.debug(f"Sending command: {cmd_type} with params: {params}")

        # Send command to server
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((self.host, self.port))
                sock.sendall(json.dumps(command).encode("utf-8"))

                # Read incrementally; stop as soon as a complete JSON
                # response parses out of the buffer. The server keeps the
                # connection open afterwards (see docstring above), so a
                # chunk-until-EOF loop would deadlock here.
                #
                # Catch UnicodeDecodeError as well as JSONDecodeError: a
                # multi-byte UTF-8 character (e.g. an emoji or non-ASCII
                # object name in an error message) can be split across
                # ``recv()`` boundaries, raising UnicodeDecodeError on the
                # partial buffer. Treat both as "keep reading".
                buffer = b""
                parsed_response = None
                while True:
                    try:
                        chunk = sock.recv(65536)
                    except ConnectionResetError as e:
                        # RST instead of FIN — server crashed or closed
                        # forcefully mid-response. Same user-facing
                        # outcome: we don't have a complete response.
                        raise MCPError(
                            "Connection closed before a complete response was received"
                        ) from e
                    if not chunk:
                        # FIN before a complete JSON response.
                        raise MCPError(
                            "Connection closed before a complete response was received"
                        )
                    buffer += chunk
                    try:
                        parsed_response = json.loads(buffer.decode("utf-8"))
                        break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

                if parsed_response["status"] == "error":
                    error_message = parsed_response.get("message", "Unknown error")
                    enhanced_message = self._enhance_error_message(error_message)
                    self.log.error(f"Error response: {error_message}")
                    raise MCPError(enhanced_message)
                else:
                    self.log.debug(f"Response status: {parsed_response['status']}")

                return parsed_response
        except ConnectionRefusedError:
            error_msg = "Connection refused. Is the Blender MCP server running?"
            self.log.error(f"Connection error: {error_msg}")
            raise MCPError(error_msg)
        except socket.timeout:
            error_msg = (
                f"Timed out after {timeout}s waiting for response from Blender MCP "
                f"server at {self.host}:{self.port}. The server may be unresponsive — "
                "check Blender's console for errors."
            )
            self.log.error(error_msg)
            raise MCPError(error_msg)
        except MCPError:
            # Re-raise MCPError without wrapping it
            raise
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.log.error(error_msg)
            raise MCPError(error_msg)

    def execute_code(self, code):
        self.log.debug("Executing code in Blender")
        return self.send_command("execute_code", {"code": code})

    def get_scene_info(self):
        self.log.debug("Getting scene info")
        return self.send_command("get_scene_info")

    def create_object(
        self,
        type="CUBE",
        name=None,
        location=(0, 0, 0),
        rotation=(0, 0, 0),
        scale=(1, 1, 1),
    ):
        params = {
            "type": type,
            "location": location,
            "rotation": rotation,
            "scale": scale,
        }
        if name:
            params["name"] = name
        self.log.debug(f"Creating {type} object{' named ' + name if name else ''}")
        return self.send_command("create_object", params)

    def modify_object(
        self, name, location=None, rotation=None, scale=None, visible=None
    ):
        params = {"name": name}
        if location is not None:
            params["location"] = location
        if rotation is not None:
            params["rotation"] = rotation
        if scale is not None:
            params["scale"] = scale
        if visible is not None:
            params["visible"] = visible
        self.log.debug(f"Modifying object '{name}'")
        return self.send_command("modify_object", params)

    def delete_object(self, name):
        self.log.debug(f"Deleting object '{name}'")
        return self.send_command("delete_object", {"name": name})

    def get_object_info(self, name):
        self.log.debug(f"Getting info for object '{name}'")
        return self.send_command("get_object_info", {"name": name})
