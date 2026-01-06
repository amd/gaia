# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

import subprocess
import time


def kill_process_on_port(port):
    """Kill any process running on the specified port."""
    try:
        # Find process using the port
        result = subprocess.run(
            f"netstat -ano | findstr :{port}",
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.stdout:
            # Extract PID
            pids_to_kill = set()
            for line in result.stdout.strip().split("\n"):
                if f":{port}" in line and (
                    "LISTENING" in line or "ESTABLISHED" in line
                ):
                    parts = line.strip().split()
                    if len(parts) > 4:
                        pid = parts[-1]
                        pids_to_kill.add(pid)

            # Kill each process found
            for pid in pids_to_kill:
                print(f"Found process with PID {pid} on port {port}")
                try:
                    # Kill the process
                    subprocess.run(f"taskkill /F /PID {pid}", shell=True, check=False)
                    print(f"Killed process with PID {pid}")
                except Exception as e:
                    print(f"Error killing PID {pid}: {e}")

            # Give the OS some time to free the port
            if pids_to_kill:
                time.sleep(2)
    except Exception as e:
        print(f"Error killing process on port {port}: {e}")
