# This Blender MCP client is a simplified and modified version of the BlenderMCP project from https://github.com/BlenderMCP/blender-mcp

import socket
import json
import logging
import re
from gaia.logger import get_logger


class MCPError(Exception):
    """Exception raised for MCP client errors."""

    pass


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

    def send_command(self, cmd_type, params=None):
        if params is None:
            params = {}

        # Create command
        command = {"type": cmd_type, "params": params}

        self.log.debug(f"Sending command: {cmd_type} with params: {params}")

        # Send command to server
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect((self.host, self.port))
                sock.sendall(json.dumps(command).encode("utf-8"))

                # Receive the response
                response = sock.recv(65536).decode("utf-8")

                # Parse the JSON response
                parsed_response = json.loads(response)

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
        except MCPError:
            # Re-raise MCPError without wrapping it
            raise
        except Exception as e:
            error_msg = f"Error: {str(e)}"
            self.log.error(error_msg)
            raise MCPError(error_msg)

    def execute_code(self, code):
        self.log.debug(f"Executing code in Blender")
        return self.send_command("execute_code", {"code": code})

    def get_scene_info(self):
        self.log.debug(f"Getting scene info")
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
