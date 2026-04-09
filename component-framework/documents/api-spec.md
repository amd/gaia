---
template_id: api-spec
template_type: documents
version: 1.0.0
created: "2026-04-07"
updated: "2026-04-07"
maintainer: pipeline-engine
description: API specification template for endpoint documentation
schema_version: "1.0"
---

# API Specification: {{API_NAME}}

## Purpose

This template documents API endpoints, request/response schemas, authentication requirements, and error handling for programmatic interfaces.

## Overview

**API Name:** {{API_NAME}}

**Version:** {{VERSION}}

**Base URL:** `{{BASE_URL}}`

**Status:** Draft | Stable | Deprecated

**Last Updated:** {{TIMESTAMP}}

**Maintainer:** {{MAINTAINER}}

## Authentication

| Method | Type | Description | Header |
|--------|------|-------------|--------|
| {{METHOD}} | {{TYPE}} | {{DESC}} | {{HEADER}} |

### Authentication Requirements

[Details on how to authenticate]

```
Authorization: Bearer {{TOKEN}}
```

## Endpoints

### Summary

| Method | Path | Description | Auth Required | Rate Limit |
|--------|------|-------------|---------------|------------|
| {{METHOD}} | {{PATH}} | {{DESC}} | {{AUTH}} | {{LIMIT}} |

### Endpoint Details

#### {{METHOD}} {{PATH}}

**Summary:**
[Brief description of what this endpoint does]

**Description:**
[Detailed description including behavior and side effects]

**Authentication:** Required | Not Required

**Rate Limit:** {{LIMIT}} requests per {{PERIOD}}

##### Path Parameters

| Name | Type | Required | Description | Example |
|------|------|----------|-------------|---------|
| {{NAME}} | {{TYPE}} | {{REQUIRED}} | {{DESC}} | {{EXAMPLE}} |

##### Query Parameters

| Name | Type | Required | Default | Description |
|------|------|----------|---------|-------------|
| {{NAME}} | {{TYPE}} | {{REQUIRED}} | {{DEFAULT}} | {{DESC}} |

##### Request Headers

| Header | Type | Required | Description |
|--------|------|----------|-------------|
| {{HEADER}} | {{TYPE}} | {{REQUIRED}} | {{DESC}} |

##### Request Body

**Content-Type:** `application/json`

**Schema:**
```yaml
{{REQUEST_SCHEMA}}
```

**Example:**
```json
{{REQUEST_EXAMPLE}}
```

##### Response: 200 OK

**Content-Type:** `application/json`

**Schema:**
```yaml
{{RESPONSE_SCHEMA}}
```

**Example:**
```json
{{RESPONSE_EXAMPLE}}
```

##### Response: 400 Bad Request

**Schema:**
```yaml
{{ERROR_SCHEMA}}
```

##### Response: 401 Unauthorized

```json
{
  "error": "Unauthorized",
  "message": "Authentication required"
}
```

##### Response: 404 Not Found

```json
{
  "error": "Not Found",
  "message": "Resource not found"
}
```

##### Response: 500 Internal Server Error

```json
{
  "error": "Internal Server Error",
  "message": "An unexpected error occurred"
}
```

## Error Codes

| Code | HTTP Status | Meaning | Recovery Action |
|------|-------------|---------|-----------------|
| {{CODE}} | {{STATUS}} | {{MEANING}} | {{RECOVERY}} |

### Error Response Format

```json
{
  "error": {
    "code": "{{ERROR_CODE}}",
    "message": "{{MESSAGE}}",
    "details": {}
  }
}
```

## Data Models

### {{MODEL_NAME}}

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| {{FIELD}} | {{TYPE}} | {{REQUIRED}} | {{DESC}} |

### Enumerations

#### {{ENUM_NAME}}

| Value | Description |
|-------|-------------|
| {{VALUE}} | {{DESC}} |

## Rate Limiting

| Endpoint | Limit | Window | Headers |
|----------|-------|--------|---------|
| {{ENDPOINT}} | {{LIMIT}} | {{WINDOW}} | {{HEADERS}} |

### Rate Limit Headers

| Header | Description |
|--------|-------------|
| X-RateLimit-Limit | Maximum requests allowed |
| X-RateLimit-Remaining | Requests remaining in window |
| X-RateLimit-Reset | Unix timestamp when window resets |

## Pagination

[How pagination works for list endpoints]

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| page | integer | 1 | Page number |
| page_size | integer | 20 | Items per page |

**Response Format:**
```json
{
  "data": [],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total_items": 100,
    "total_pages": 5
  }
}
```

## Versioning

[How API versions are managed]

**Current Version:** {{VERSION}}

**Version Format:** `v{major}` in URL path

**Deprecation Policy:**
[How deprecated versions are handled]

## Examples

### Example: {{EXAMPLE_NAME}}

**Description:**
[What this example demonstrates]

**Request:**
```bash
curl -X {{METHOD}} "{{BASE_URL}}{{PATH}}" \
  -H "Authorization: Bearer {{TOKEN}}" \
  -H "Content-Type: application/json" \
  -d '{{BODY}}'
```

**Response:**
```json
{{RESPONSE}}
```

## Related Components

- [[component-framework/documents/design-doc.md]] - For design context
- [[component-framework/commands/shell-commands.md]] - For curl examples
- [[component-framework/knowledge/domain-knowledge.md]] - For API domain context
