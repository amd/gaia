---
name: triage-support-ticket
description: Triage an inbound support ticket end to end. Use when the user pastes a support ticket or asks to triage one.
version: 0.2.0
metadata:
  gaia:
    tools_required:
      - query_documents
      - read_file
      - remember
---

# Triage a Support Ticket

1. Pull matching policy docs with `query_documents`.
2. Read any attached log with `read_file`.
3. Record the disposition with `remember` for follow-up.
