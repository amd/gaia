# This Blender MCP client is a simplified and modified version of the BlenderMCP project from https://github.com/BlenderMCP/blender-mcp

import bpy
import mathutils
import json
import threading
import socket
import time
import traceback
from bpy.props import IntProperty, BoolProperty

bl_info = {
    "name": "Simple Blender MCP",
    "author": "BlenderMCP",
    "version": (0, 3),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > BlenderMCP",
    "description": "Connect Blender via MCP",
    "category": "Interface",
}


class SimpleBlenderMCPServer:
    def __init__(self, host="localhost", port=9876):
        self.host = host
        self.port = port
        self.running = False
        self.socket = None
        self.server_thread = None

    def start(self):
        if self.running:
            print("Server is already running")
            return

        self.running = True

        try:
            # Create socket
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((self.host, self.port))
            self.socket.listen(1)

            # Start server thread
            self.server_thread = threading.Thread(target=self._server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            print(f"SimpleMCP server started on {self.host}:{self.port}")
        except Exception as e:
            print(f"Failed to start server: {str(e)}")
            self.stop()

    def stop(self):
        self.running = False

        # Close socket
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
            self.socket = None

        # Wait for thread to finish
        if self.server_thread:
            try:
                if self.server_thread.is_alive():
                    self.server_thread.join(timeout=1.0)
            except:
                pass
            self.server_thread = None

        print("SimpleMCP server stopped")

    def _server_loop(self):
        """Main server loop in a separate thread"""
        print("Server thread started")
        self.socket.settimeout(1.0)  # Timeout to allow for stopping

        while self.running:
            try:
                # Accept new connection
                try:
                    client, address = self.socket.accept()
                    print(f"Connected to client: {address}")

                    # Handle client in a separate thread
                    client_thread = threading.Thread(
                        target=self._handle_client, args=(client,)
                    )
                    client_thread.daemon = True
                    client_thread.start()
                except socket.timeout:
                    # Just check running condition
                    continue
                except Exception as e:
                    print(f"Error accepting connection: {str(e)}")
                    time.sleep(0.5)
            except Exception as e:
                print(f"Error in server loop: {str(e)}")
                if not self.running:
                    break
                time.sleep(0.5)

        print("Server thread stopped")

    def _handle_client(self, client):
        """Handle connected client"""
        print("Client handler started")
        client.settimeout(None)  # No timeout
        buffer = b""

        try:
            while self.running:
                # Receive data
                try:
                    data = client.recv(8192)
                    if not data:
                        print("Client disconnected")
                        break

                    buffer += data
                    try:
                        # Try to parse command
                        command = json.loads(buffer.decode("utf-8"))
                        buffer = b""

                        # Execute command in Blender's main thread
                        def execute_wrapper():
                            try:
                                response = self.execute_command(command)
                                response_json = json.dumps(response)
                                try:
                                    client.sendall(response_json.encode("utf-8"))
                                except:
                                    print(
                                        "Failed to send response - client disconnected"
                                    )
                            except Exception as e:
                                print(f"Error executing command: {str(e)}")
                                traceback.print_exc()
                                try:
                                    error_response = {
                                        "status": "error",
                                        "message": str(e),
                                    }
                                    client.sendall(
                                        json.dumps(error_response).encode("utf-8")
                                    )
                                except:
                                    pass
                            return None

                        # Schedule execution in main thread
                        bpy.app.timers.register(execute_wrapper, first_interval=0.0)
                    except json.JSONDecodeError:
                        # Incomplete data, wait for more
                        pass
                except Exception as e:
                    print(f"Error receiving data: {str(e)}")
                    break
        except Exception as e:
            print(f"Error in client handler: {str(e)}")
        finally:
            try:
                client.close()
            except:
                pass
            print("Client handler stopped")

    def execute_command(self, command):
        """Execute a command in the main Blender thread"""
        try:
            cmd_type = command.get("type")
            params = command.get("params", {})

            # Ensure we're in the right context
            if cmd_type in ["create_object", "modify_object", "delete_object"]:
                override = bpy.context.copy()
                override["area"] = [
                    area for area in bpy.context.screen.areas if area.type == "VIEW_3D"
                ][0]
                with bpy.context.temp_override(**override):
                    return self._execute_command_internal(command)
            else:
                return self._execute_command_internal(command)

        except Exception as e:
            print(f"Error executing command: {str(e)}")
            traceback.print_exc()
            return {"status": "error", "message": str(e)}

    def _execute_command_internal(self, command):
        """Internal command execution with proper context"""
        cmd_type = command.get("type")
        params = command.get("params", {})

        # Define available command handlers
        handlers = {
            "get_scene_info": self.get_scene_info,
            "create_object": self.create_object,
            "modify_object": self.modify_object,
            "delete_object": self.delete_object,
            "get_object_info": self.get_object_info,
            "execute_code": self.execute_code,
        }

        handler = handlers.get(cmd_type)
        if handler:
            try:
                print(f"Executing handler for {cmd_type}")
                result = handler(**params)
                print(f"Handler execution complete")
                return {"status": "success", "result": result}
            except Exception as e:
                print(f"Error in handler: {str(e)}")
                traceback.print_exc()
                return {"status": "error", "message": str(e)}
        else:
            return {"status": "error", "message": f"Unknown command type: {cmd_type}"}

    def get_scene_info(self):
        """Get information about the current Blender scene"""
        try:
            print("Getting scene info...")
            # Simplify the scene info to reduce data size
            scene_info = {
                "name": bpy.context.scene.name,
                "object_count": len(bpy.context.scene.objects),
                "objects": [],
            }

            # Collect minimal object information (limit to first 10 objects)
            for i, obj in enumerate(bpy.context.scene.objects):
                if i >= 10:
                    break

                obj_info = {
                    "name": obj.name,
                    "type": obj.type,
                    # Only include basic location data
                    "location": [
                        round(float(obj.location.x), 2),
                        round(float(obj.location.y), 2),
                        round(float(obj.location.z), 2),
                    ],
                }
                scene_info["objects"].append(obj_info)

            print(f"Scene info collected: {len(scene_info['objects'])} objects")
            return scene_info
        except Exception as e:
            print(f"Error in get_scene_info: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    @staticmethod
    def _get_aabb(obj):
        """Returns the world-space axis-aligned bounding box (AABB) of an object."""
        if obj.type != "MESH":
            raise TypeError("Object must be a mesh")

        # Get the bounding box corners in local space
        local_bbox_corners = [mathutils.Vector(corner) for corner in obj.bound_box]

        # Convert to world coordinates
        world_bbox_corners = [
            obj.matrix_world @ corner for corner in local_bbox_corners
        ]

        # Compute axis-aligned min/max coordinates
        min_corner = mathutils.Vector(map(min, zip(*world_bbox_corners)))
        max_corner = mathutils.Vector(map(max, zip(*world_bbox_corners)))

        return [[*min_corner], [*max_corner]]

    def create_object(
        self,
        type="CUBE",
        name=None,
        location=(0, 0, 0),
        rotation=(0, 0, 0),
        scale=(1, 1, 1),
    ):
        """Create a new object in the scene"""
        try:
            # Deselect all objects first
            bpy.ops.object.select_all(action="DESELECT")

            # Create the object based on type
            if type == "CUBE":
                bpy.ops.mesh.primitive_cube_add(
                    location=location, rotation=rotation, scale=scale
                )
            elif type == "SPHERE":
                bpy.ops.mesh.primitive_uv_sphere_add(
                    location=location, rotation=rotation, scale=scale
                )
            elif type == "CYLINDER":
                bpy.ops.mesh.primitive_cylinder_add(
                    location=location, rotation=rotation, scale=scale
                )
            elif type == "PLANE":
                bpy.ops.mesh.primitive_plane_add(
                    location=location, rotation=rotation, scale=scale
                )
            elif type == "CONE":
                bpy.ops.mesh.primitive_cone_add(
                    location=location, rotation=rotation, scale=scale
                )
            elif type == "EMPTY":
                bpy.ops.object.empty_add(
                    location=location, rotation=rotation, scale=scale
                )
            elif type == "CAMERA":
                bpy.ops.object.camera_add(location=location, rotation=rotation)
            elif type == "LIGHT":
                bpy.ops.object.light_add(
                    type="POINT", location=location, rotation=rotation, scale=scale
                )
            else:
                raise ValueError(f"Unsupported object type: {type}")

            # Force update the view layer
            bpy.context.view_layer.update()

            # Get the active object (which should be our newly created object)
            obj = bpy.context.view_layer.objects.active

            # If we don't have an active object, something went wrong
            if obj is None:
                raise RuntimeError("Failed to create object - no active object")

            # Make sure it's selected
            obj.select_set(True)

            # Rename if name is provided
            if name:
                obj.name = name
                if obj.data:
                    obj.data.name = name

            # Return the object info
            result = {
                "name": obj.name,
                "type": obj.type,
                "location": [obj.location.x, obj.location.y, obj.location.z],
                "rotation": [
                    obj.rotation_euler.x,
                    obj.rotation_euler.y,
                    obj.rotation_euler.z,
                ],
                "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            }

            if obj.type == "MESH":
                bounding_box = self._get_aabb(obj)
                result["world_bounding_box"] = bounding_box

            return result
        except Exception as e:
            print(f"Error in create_object: {str(e)}")
            traceback.print_exc()
            return {"error": str(e)}

    def modify_object(
        self, name, location=None, rotation=None, scale=None, visible=None
    ):
        """Modify an existing object in the scene"""
        # Find the object by name
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Modify properties as requested
        if location is not None:
            obj.location = location

        if rotation is not None:
            obj.rotation_euler = rotation

        if scale is not None:
            obj.scale = scale

        if visible is not None:
            obj.hide_viewport = not visible
            obj.hide_render = not visible

        result = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            result["world_bounding_box"] = bounding_box

        return result

    def delete_object(self, name):
        """Delete an object from the scene"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Store the name to return
        obj_name = obj.name

        # Select and delete the object
        if obj:
            bpy.data.objects.remove(obj, do_unlink=True)

        return {"deleted": obj_name}

    def get_object_info(self, name):
        """Get detailed information about a specific object"""
        obj = bpy.data.objects.get(name)
        if not obj:
            raise ValueError(f"Object not found: {name}")

        # Basic object info
        obj_info = {
            "name": obj.name,
            "type": obj.type,
            "location": [obj.location.x, obj.location.y, obj.location.z],
            "rotation": [
                obj.rotation_euler.x,
                obj.rotation_euler.y,
                obj.rotation_euler.z,
            ],
            "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
            "visible": obj.visible_get(),
        }

        if obj.type == "MESH":
            bounding_box = self._get_aabb(obj)
            obj_info["world_bounding_box"] = bounding_box

            # Add mesh data if applicable
            mesh = obj.data
            obj_info["mesh"] = {
                "vertices": len(mesh.vertices),
                "edges": len(mesh.edges),
                "polygons": len(mesh.polygons),
            }

        return obj_info

    def execute_code(self, code):
        """Execute arbitrary Blender Python code"""
        try:
            # Create a namespace for execution and a buffer to capture output
            import io
            import sys
            from contextlib import redirect_stdout, redirect_stderr

            namespace = {"bpy": bpy}
            stdout_buffer = io.StringIO()
            stderr_buffer = io.StringIO()
            result_value = None

            # Print to Blender console that we're executing code
            print("\n----- EXECUTING CODE IN BLENDER -----")
            print(code)
            print("----- CODE EXECUTION OUTPUT -----")

            # Class to split output between buffer and console
            class TeeOutput:
                def __init__(self, buffer, original):
                    self.buffer = buffer
                    self.original = original

                def write(self, text):
                    self.buffer.write(text)
                    self.original.write(text)

                def flush(self):
                    self.original.flush()

            # Setup tee for both stdout and stderr
            stdout_tee = TeeOutput(stdout_buffer, sys.__stdout__)
            stderr_tee = TeeOutput(stderr_buffer, sys.__stderr__)

            # Execute the code and capture output and return value
            with redirect_stdout(stdout_tee), redirect_stderr(stderr_tee):
                exec_result = exec(code, namespace)
                if "result" in namespace:
                    result_value = namespace["result"]

            # Get the captured output
            stdout_output = stdout_buffer.getvalue()
            stderr_output = stderr_buffer.getvalue()

            # Print execution completion to console
            print("----- CODE EXECUTION COMPLETE -----")
            if result_value:
                print("----- RETURNED RESULT -----")
                print(str(result_value))
            print("\n")

            # Return a more detailed response
            return {
                "executed": True,
                "stdout": stdout_output,
                "stderr": stderr_output,
                "result": result_value,
            }
        except Exception as e:
            import traceback

            tb_str = traceback.format_exc()
            # Print error to console
            print("----- CODE EXECUTION ERROR -----")
            print(str(e))
            print(tb_str)
            print("--------------------------------")
            raise Exception(f"Code execution error: {str(e)}\n{tb_str}")


# Blender UI Panel
class SIMPLEMCP_PT_Panel(bpy.types.Panel):
    bl_label = "Blender MCP"
    bl_idname = "SIMPLEMCP_PT_Panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "BlenderMCP"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.prop(scene, "simplemcp_port")

        if not scene.simplemcp_server_running:
            layout.operator("simplemcp.start_server", text="Start MCP Server")
        else:
            layout.operator("simplemcp.stop_server", text="Stop MCP Server")
            layout.label(text=f"Running on port {scene.simplemcp_port}")


# Operator to start the server
class SIMPLEMCP_OT_StartServer(bpy.types.Operator):
    bl_idname = "simplemcp.start_server"
    bl_label = "Connect to GAIA"
    bl_description = "Start the BlenderMCP server to connect to GAIA"

    def execute(self, context):
        scene = context.scene

        # Create a new server instance
        if not hasattr(bpy.types, "simplemcp_server") or not bpy.types.simplemcp_server:
            bpy.types.simplemcp_server = SimpleBlenderMCPServer(
                port=scene.simplemcp_port
            )

        # Start the server
        bpy.types.simplemcp_server.start()
        scene.simplemcp_server_running = True

        return {"FINISHED"}


# Operator to stop the server
class SIMPLEMCP_OT_StopServer(bpy.types.Operator):
    bl_idname = "simplemcp.stop_server"
    bl_label = "Stop the connection"
    bl_description = "Stop the connection"

    def execute(self, context):
        scene = context.scene

        # Stop the server if it exists
        if hasattr(bpy.types, "simplemcp_server") and bpy.types.simplemcp_server:
            bpy.types.simplemcp_server.stop()
            del bpy.types.simplemcp_server

        scene.simplemcp_server_running = False

        return {"FINISHED"}


# Registration functions
def register():
    bpy.types.Scene.simplemcp_port = IntProperty(
        name="Port",
        description="Port for the BlenderMCP server",
        default=9876,
        min=1024,
        max=65535,
    )

    bpy.types.Scene.simplemcp_server_running = BoolProperty(
        name="Server Running", default=False
    )

    bpy.utils.register_class(SIMPLEMCP_PT_Panel)
    bpy.utils.register_class(SIMPLEMCP_OT_StartServer)
    bpy.utils.register_class(SIMPLEMCP_OT_StopServer)

    print("BlenderMCP addon registered")


def unregister():
    # Stop the server if it's running
    if hasattr(bpy.types, "simplemcp_server") and bpy.types.simplemcp_server:
        try:
            bpy.types.simplemcp_server.stop()
            del bpy.types.simplemcp_server
        except Exception as e:
            print(f"Error stopping server: {str(e)}")
            traceback.print_exc()

    try:
        bpy.utils.unregister_class(SIMPLEMCP_PT_Panel)
        bpy.utils.unregister_class(SIMPLEMCP_OT_StartServer)
        bpy.utils.unregister_class(SIMPLEMCP_OT_StopServer)
    except Exception as e:
        print(f"Error unregistering classes: {str(e)}")
        traceback.print_exc()

    try:
        del bpy.types.Scene.simplemcp_port
        del bpy.types.Scene.simplemcp_server_running
    except Exception as e:
        print(f"Error removing properties: {str(e)}")
        traceback.print_exc()

    print("BlenderMCP addon unregistered")


if __name__ == "__main__":
    register()
