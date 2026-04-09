---
template_id: knowledge-graph
template_type: knowledge
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: Knowledge graph template for structured knowledge relationships
schema_version: "1.0"
---

# Knowledge Graph

## Purpose

This template provides a structured representation of knowledge as a graph with nodes (entities/concepts) and edges (relationships), enabling complex queries and reasoning.

## Graph Overview

**Graph Name:** {{GRAPH_NAME}}

**Domain:** {{DOMAIN}}

**Created:** {{DATE}}

**Last Updated:** {{TIMESTAMP}}

**Total Nodes:** {{NODE_COUNT}}

**Total Edges:** {{EDGE_COUNT}}

## Nodes

| Node ID | Type | Label | Properties | Created |
|---------|------|-------|------------|---------|
| {{ID}} | {{TYPE}} | {{LABEL}} | {{PROPS}} | {{DATE}} |

### Node Types

| Type | Description | Count | Example |
|------|-------------|-------|---------|
| {{TYPE}} | {{DESC}} | {{COUNT}} | {{EXAMPLE}} |

### Node Details

#### Node: {{NODE_ID}}

**Basic Information:**
- **Node ID:** {{NODE_ID}}
- **Type:** {{NODE_TYPE}}
- **Label:** {{LABEL}}
- **Description:** [Detailed description]

**Properties:**
| Property | Value | Type |
|----------|-------|------|
| {{PROP}} | {{VALUE}} | {{TYPE}} |

**Metadata:**
- **Created:** {{DATE}}
- **Created By:** {{AGENT_ID}}
- **Last Modified:** {{DATE}}
- **Confidence:** 0.0 - 1.0

## Edges

| Edge ID | Source | Relationship | Target | Weight | Properties |
|---------|--------|--------------|--------|--------|------------|
| {{ID}} | {{SRC}} | {{REL}} | {{TGT}} | {{WEIGHT}} | {{PROPS}} |

### Relationship Types

| Relationship | Description | Domain | Range |
|--------------|-------------|--------|-------|
| {{REL}} | {{DESC}} | {{DOMAIN}} | {{RANGE}} |

### Edge Details

#### Edge: {{EDGE_ID}}

- **Edge ID:** {{EDGE_ID}}
- **Source Node:** {{SOURCE_ID}}
- **Target Node:** {{TARGET_ID}}
- **Relationship:** {{RELATIONSHIP_TYPE}}
- **Direction:** Directed | Undirected
- **Weight:** {{WEIGHT}}

**Properties:**
| Property | Value |
|----------|-------|
| {{PROP}} | {{VALUE}} |

## Subgraphs

[Logical groupings within the graph]

### Subgraph: {{SUBGRAPH_NAME}}

- **Description:** [Purpose of this subgraph]
- **Nodes:** [List of node IDs]
- **Root Node:** {{NODE_ID}}

## Queries

[Common queries against this graph]

### Query: {{QUERY_NAME}}

**Purpose:**
[What this query finds]

**Query Pattern:**
```
{{QUERY_PATTERN}}
```

**Example Result:**
```
{{EXAMPLE_RESULT}}
```

## Graph Statistics

| Metric | Value |
|--------|-------|
| Total Nodes | {{COUNT}} |
| Total Edges | {{COUNT}} |
| Node Types | {{COUNT}} |
| Edge Types | {{COUNT}} |
| Average Degree | {{AVG}} |
| Density | {{DENSITY}} |

## Visual Representation

[ASCII or description of graph structure]

```
{{GRAPH_VISUALIZATION}}
```

## Related Components

- [[component-framework/knowledge/declarative-knowledge.md]] - For fact assertions
- [[component-framework/knowledge/domain-knowledge.md]] - For domain context
- [[component-framework/memory/long-term-memory.md]] - For learned patterns
