---
name: local-capability
description: Writes files on the local workspace. Used to prove Phase 1 refuses local-capability permissions.
version: 0.1.0
metadata:
  gaia:
    security_tier: community
    permissions:
      - filesystem:write:./**
---

# Local Capability

Loading this skill must be refused until the permission sandbox ships.
