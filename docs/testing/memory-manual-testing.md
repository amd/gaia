# Memory System — Manual Testing Guide

The magic of memory is when GAIA feels like it truly *knows* you — your preferences,
your projects, your patterns. This guide walks through realistic workflows where
memory transforms a stateless chatbot into a personalized assistant.

## Setup

```powershell
$env:GAIA_MEMORY_ADMIN="1"; $env:GAIA_MEMORY_MCP_ALWAYS="1"; gaia chat --ui
```

Open the Memory Dashboard between scenarios to watch knowledge accumulate.

---

## Scenario 1: First-Time Setup

**The magic:** GAIA learns who you are and remembers it forever.

1. Open Memory Dashboard > Settings
2. Toggle "System discovery" ON — watch it learn your hardware, OS, software
3. Start a new chat session:
   - "Hi, I'm Alex. I'm a backend engineer working on microservices at Acme Corp"
   - "I mainly work with Python and Go, and I prefer VS Code with vim keybindings"
   - "I like concise answers with code examples, no fluff"
4. **Close the session. Open a new one.**
5. "What do you know about me?"
6. **Magic moment:** GAIA should recall your name, role, languages, editor, and communication preference — without being told again.

**Dashboard check:** Look for preference, fact, and note items with entity tags.

---

## Scenario 2: Project Context + File Work

**The magic:** GAIA remembers your project context while helping with files.

1. "I'm working on project Atlas — it's a REST API for inventory management using FastAPI"
2. "Create a file called `inventory.py` with a basic FastAPI app that has GET /items and POST /items endpoints"
3. Verify the file was created and looks correct
4. "Read the file back and add input validation with Pydantic models"
5. "Take a note: Atlas API uses PostgreSQL 15 on port 5433, not the default"
6. **New session:**
7. "What database does project Atlas use?"
8. **Magic moment:** Recalls PostgreSQL 15 on port 5433 without you re-explaining.
9. "Read inventory.py and add a database connection using the right port"
10. **Magic moment:** Uses port 5433 from memory, not the default 5432.

**Dashboard check:** Note items about Atlas, file operations in tool history.

---

## Scenario 3: Research + Web Search + Notes

**The magic:** GAIA captures research findings as persistent knowledge.

1. "Search the web for the latest FastAPI best practices in 2026"
2. "Take a note on the key findings from that search"
3. "Search for how to structure a large FastAPI project with multiple routers"
4. "Note: Based on my research, we should use the domain-driven folder structure for Atlas"
5. **New session:**
6. "What architecture decision did I make for the Atlas project?"
7. **Magic moment:** Recalls the domain-driven structure decision from your research session.

**Dashboard check:** Notes should have meaningful content, not just "user searched for X".

---

## Scenario 4: Todo List Across Sessions

**The magic:** A persistent todo list that survives session boundaries.

1. "Add to my todo: implement JWT authentication for Atlas"
2. "Add to my todo: write integration tests for the /items endpoints"
3. "Add to my todo: set up CI/CD pipeline with GitHub Actions"
4. "Add to my todo: review PR #42 from Sarah"
5. "What's on my todo list?"
6. Verify all 4 items listed
7. "I finished the JWT auth, mark it done"
8. **New session:**
9. "What's still on my todo list?"
10. **Magic moment:** Shows 3 remaining items, JWT auth is gone.
11. "Actually, Sarah's PR was already merged. Remove that from my list"
12. "Show my todos"
13. Should show 2 remaining items

**Dashboard check:** Knowledge items with category "todo", updated timestamps.

---

## Scenario 5: Reminders with Due Dates

**The magic:** GAIA proactively surfaces upcoming deadlines.

1. "Remind me to renew the SSL certificate by June 15th"
2. "Remind me about the team demo next Friday"
3. "Remind me to submit the expense report by end of month"
4. "What's coming up?"
5. Should list all 3 with due dates
6. **New session:**
7. "Do I have any upcoming deadlines?"
8. **Magic moment:** Proactively mentions the SSL cert, demo, and expense report with dates.

**Dashboard check:** Temporal tab shows upcoming items. Knowledge items have `due_at` set.

---

## Scenario 6: Learning Your Preferences Through Conversation

**The magic:** GAIA picks up on implicit preferences, not just explicit "remember this" commands.

1. "Can you write a Python function that checks if a number is prime?"
2. (Agent writes a function)
3. "I prefer type hints on everything and Google-style docstrings"
4. "Rewrite it with those conventions"
5. **New session:**
6. "Write a function that calculates fibonacci numbers"
7. **Magic moment:** Should use type hints and Google-style docstrings automatically, without being asked.

**Dashboard check:** Preference items about type hints and docstring style.

---

## Scenario 7: Contact Profiles

**The magic:** GAIA builds profiles of people you mention.

1. "Sarah Chen is our DevOps lead. She prefers Terraform over Pulumi"
2. "Marcus handles the frontend — he's the React expert on the team"
3. "Sarah's on PTO next week, so I need to handle the deploy myself"
4. **New session:**
5. "Who should I ask about our Terraform setup?"
6. **Magic moment:** Recommends Sarah Chen, mentions she's the DevOps lead who prefers Terraform.
7. "What do I know about my team?"
8. Should compile profiles for Sarah and Marcus.

**Dashboard check:** Entity-linked items (person:sarah_chen, person:marcus).

---

## Scenario 8: Error Learning

**The magic:** GAIA remembers mistakes and avoids them next time.

1. "Run `python -c \"import pandas\"`" (assume it fails — pandas not installed)
2. "Note: On this machine, we use polars instead of pandas"
3. **New session:**
4. "Read the CSV file at data/sales.csv and show me a summary"
5. **Magic moment:** Should reach for polars (not pandas) based on the stored preference.

**Dashboard check:** Tool history shows the failed pandas command; knowledge has the polars preference.

---

## Scenario 9: Data Analysis with Scratchpad

**The magic:** Memory retains context about your datasets across sessions.

1. "Create a scratchpad table called 'sales' with columns: date, product, revenue, region"
2. "Insert some sample data: 3 rows of Q1 sales for Widget A in US, EU, APAC"
3. "Query: what's the total revenue by region?"
4. "Take a note: Widget A performs best in APAC region based on Q1 data"
5. **New session:**
6. "What did I learn about Widget A sales?"
7. **Magic moment:** Recalls the APAC insight from the data analysis session.

**Dashboard check:** Note about Widget A, tool history showing scratchpad operations.

---

## Scenario 10: Conflict Resolution

**The magic:** GAIA handles corrections gracefully, not stubbornly.

1. "Our API gateway runs on port 8080 with basic HTTP auth"
2. (Later) "We migrated the gateway to port 443 with OAuth 2.0"
3. "What port does our API gateway use?"
4. **Magic moment:** Says 443 with OAuth 2.0, not the old 8080/basic auth.
5. **Dashboard check:** Old fact should be marked as superseded.

---

## Scenario 11: Screenshot + Memory

**The magic:** GAIA remembers what it saw in screenshots.

1. Take a screenshot of a dashboard or error dialog
2. "Take a screenshot and analyze what you see"
3. "Note: The monitoring dashboard shows 3 alerts for high CPU on prod-web-02"
4. **New session:**
5. "What was the last issue I noticed on the monitoring dashboard?"
6. **Magic moment:** Recalls the CPU alerts on prod-web-02.

---

## Scenario 12: Journaling / Meeting Notes

**The magic:** GAIA becomes your meeting secretary.

1. "Meeting notes from today's standup:"
2. "- Discussed Atlas v2 timeline, targeting July 1st launch"
3. "- Sarah needs 2 more days for the Terraform migration"
4. "- Marcus found a React hydration bug in the dashboard"
5. "- Action item: I need to review the security audit by Wednesday"
6. **New session, days later:**
7. "What happened in my last standup?"
8. **Magic moment:** Recalls the meeting notes with key points.
9. "What action items do I have from meetings?"
10. Should surface the security audit review.

---

## Scenario 13: Privacy Controls

**The magic:** Users stay in control of their data.

1. Toggle a session to "private" mode
2. Share sensitive info: "The staging DB password is hunter2"
3. End session. New non-private session:
4. "What's the staging DB password?"
5. **Magic moment:** GAIA doesn't know — private sessions don't write to memory.
6. Non-private session: "Remember my AWS account ID: 123456789"
7. "Forget my AWS account ID"
8. "What's my AWS account ID?"
9. **Magic moment:** Cleanly forgotten — not recalled.

**Dashboard check:** Knowledge browser should not contain the deleted item.

---

## Scenario 14: Re-initialize Memory

**The magic:** Clean slate, but the system rebuilds intelligently.

1. Verify dashboard shows accumulated knowledge from all previous tests
2. Go to Settings > click "Re-initialize"
3. Confirm the dialog
4. Dashboard should show empty knowledge (except system items if discovery is ON)
5. Start a new chat: "Hi, I'm Alex, the backend engineer at Acme"
6. **Magic moment:** Starts fresh — no remnants of previous identity or preferences.

---

## What to Watch For

### Good Signs
- Memory recalls are relevant and timely (not dumping everything it knows)
- Preferences are applied silently (no "based on your preference for..." preamble)
- Contradictions are resolved cleanly (new info supersedes old)
- Cross-session recall feels natural, not robotic
- The agent uses memory to inform tool usage (right port, right library, right style)

### Red Flags
- Agent says "I don't have memory" or "I can't remember across sessions"
- Agent recalls deleted/forgotten items
- Agent stores every trivial statement as a memory
- Agent mentions "based on your stored preference" excessively (should be invisible)
- Private session content leaks into non-private recall
- System discovery runs without consent toggle being ON
- Stale/superseded facts appear in responses instead of updated ones
