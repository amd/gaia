---
name: tool-mismatch
description: Declares a tool its tools.py never registers. Used to prove validation fails loudly with no partial load.
version: 0.1.0
metadata:
  gaia:
    tools:
      - name: present_tool
        description: A tool that does exist
        parameters:
          text: {type: string, required: true}
        returns: {type: object}
      - name: missing_tool
        description: A tool tools.py never registers
        parameters:
          text: {type: string, required: true}
        returns: {type: object}
---

# Tool Mismatch

Loading this skill must fail — and must leave `present_tool` unregistered too.
