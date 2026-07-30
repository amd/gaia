---
name: data-explore
description: Load messy tabular data into SQL scratchpad tables and answer questions with real queries instead of eyeballing. Use when the user has a CSV, spreadsheet, export, or pasted table and asks for totals, trends, outliers, or a breakdown.
license: MIT
version: 1.0.0
metadata:
  gaia:
    security_tier: community
    tools_required:
      - create_table
      - insert_data
      - query_data
      - list_tables
    provenance:
      source: starter-pack
---

# Data Explore

An LLM reading numbers off a table gets them subtly wrong. An LLM writing SQL
against those numbers does not. Always move the data into a table first.

## Procedure

1. **Look before you load.** Read the first few rows. Identify the columns, their
   types, and which one is the key. Say out loud what you think each column
   means and let the user correct you — a misread column poisons every later
   answer.
2. **Create the table** with `create_table(table_name, columns)`. Use explicit
   types. Store money as a number, not a string with a currency symbol; store
   dates as ISO `YYYY-MM-DD`.
3. **Insert with `insert_data(table_name, data)`.** Load everything, not a
   sample — the outliers are usually the point.
4. **Verify the load.** `query_data("SELECT COUNT(*) FROM <table>")` and compare
   to the source row count. If they differ, find out why before answering
   anything.
5. **Answer with `query_data(sql)`.** One query per question. Show the SQL you
   ran alongside the result so the user can check your reasoning.
6. **Say what the data cannot tell you.** Missing rows, nulls, and a single
   month of history are all limits worth naming.

## Cleaning rules

- Trim whitespace and normalize case before comparing text keys.
- Nulls and zeros are different. Never coerce one to the other silently.
- If a column mixes formats (dates as both `01/02/24` and `2024-02-01`),
  normalize on load and tell the user you did.

## Fork this

Pin step 2 to your recurring export's exact schema and step 5 to the five
questions you always ask of it — the skill becomes a one-command monthly report.
