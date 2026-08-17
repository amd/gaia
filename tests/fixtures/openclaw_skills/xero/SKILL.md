---
name: xero
description: |
  Xero API integration with managed OAuth. Manage contacts, invoices, payments, accounts, and run financial reports. Use this skill when users want to interact with Xero accounting data. For other third party apps, use the api-gateway skill (https://clawhub.ai/byungkyu/api-gateway).
compatibility: Requires network access and valid Maton API key
metadata:
  author: maton
  version: "1.0"
  clawdbot:
    emoji: 🧠
    requires:
      env:
        - MATON_API_KEY
---

# Xero

Access the Xero API with managed OAuth authentication. Manage contacts, invoices, payments, bank transactions, and run financial reports.

## Quick Start

```bash
# List contacts
python <<'EOF'
import urllib.request, os, json
req = urllib.request.Request('https://gateway.maton.ai/xero/api.xro/2.0/Contacts')
req.add_header('Authorization', f'Bearer {os.environ["MATON_API_KEY"]}')
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

## Base URL

```
https://gateway.maton.ai/xero/{native-api-path}
```

Replace `{native-api-path}` with the actual Xero API endpoint path. The gateway proxies requests to `api.xero.com` and automatically injects your OAuth token and Xero-Tenant-Id header.

## Authentication

All requests require the Maton API key in the Authorization header:

```
Authorization: Bearer $MATON_API_KEY
```

**Environment Variable:** Set your API key as `MATON_API_KEY`:

```bash
export MATON_API_KEY="YOUR_API_KEY"
```

### Getting Your API Key

1. Sign in or create an account at [maton.ai](https://maton.ai)
2. Go to [maton.ai/settings](https://maton.ai/settings)
3. Copy your API key

## Connection Management

Manage your Xero OAuth connections at `https://ctrl.maton.ai`.

### List Connections

```bash
python <<'EOF'
import urllib.request, os, json
req = urllib.request.Request('https://ctrl.maton.ai/connections?app=xero&status=ACTIVE')
req.add_header('Authorization', f'Bearer {os.environ["MATON_API_KEY"]}')
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

### Create Connection

```bash
python <<'EOF'
import urllib.request, os, json
data = json.dumps({'app': 'xero'}).encode()
req = urllib.request.Request('https://ctrl.maton.ai/connections', data=data, method='POST')
req.add_header('Authorization', f'Bearer {os.environ["MATON_API_KEY"]}')
req.add_header('Content-Type', 'application/json')
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

### Get Connection

```bash
python <<'EOF'
import urllib.request, os, json
req = urllib.request.Request('https://ctrl.maton.ai/connections/{connection_id}')
req.add_header('Authorization', f'Bearer {os.environ["MATON_API_KEY"]}')
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

**Response:**
```json
{
  "connection": {
    "connection_id": "21fd90f9-5935-43cd-b6c8-bde9d915ca80",
    "status": "ACTIVE",
    "creation_time": "2025-12-08T07:20:53.488460Z",
    "last_updated_time": "2026-01-31T20:03:32.593153Z",
    "url": "https://connect.maton.ai/?session_token=...",
    "app": "xero",
    "metadata": {}
  }
}
```

Open the returned `url` in a browser to complete OAuth authorization.

### Delete Connection

```bash
python <<'EOF'
import urllib.request, os, json
req = urllib.request.Request('https://ctrl.maton.ai/connections/{connection_id}', method='DELETE')
req.add_header('Authorization', f'Bearer {os.environ["MATON_API_KEY"]}')
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

### Specifying Connection

If you have multiple Xero connections, specify which one to use with the `Maton-Connection` header:

```bash
python <<'EOF'
import urllib.request, os, json
req = urllib.request.Request('https://gateway.maton.ai/xero/api.xro/2.0/Contacts')
req.add_header('Authorization', f'Bearer {os.environ["MATON_API_KEY"]}')
req.add_header('Maton-Connection', '21fd90f9-5935-43cd-b6c8-bde9d915ca80')
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

If omitted, the gateway uses the default (oldest) active connection.

## API Reference

### Contacts

#### List Contacts

```bash
GET /xero/api.xro/2.0/Contacts
```

#### Get Contact

```bash
GET /xero/api.xro/2.0/Contacts/{contactId}
```

#### Create Contact

```bash
POST /xero/api.xro/2.0/Contacts
Content-Type: application/json

{
  "Contacts": [{
    "Name": "John Doe",
    "EmailAddress": "john@example.com",
    "Phones": [{"PhoneType": "DEFAULT", "PhoneNumber": "555-1234"}]
  }]
}
```

### Invoices

#### List Invoices

```bash
GET /xero/api.xro/2.0/Invoices
```

#### Create Invoice

```bash
POST /xero/api.xro/2.0/Invoices
Content-Type: application/json

{
  "Invoices": [{
    "Type": "ACCREC",
    "Contact": {"ContactID": "xxx"},
    "LineItems": [{
      "Description": "Service",
      "Quantity": 1,
      "UnitAmount": 100.00,
      "AccountCode": "200"
    }]
  }]
}
```

### Accounts

#### List Accounts

```bash
GET /xero/api.xro/2.0/Accounts
```

### Payments

#### List Payments

```bash
GET /xero/api.xro/2.0/Payments
```

### Bank Transactions

#### List Bank Transactions

```bash
GET /xero/api.xro/2.0/BankTransactions
```

### Reports

#### Profit and Loss

```bash
GET /xero/api.xro/2.0/Reports/ProfitAndLoss?fromDate=2024-01-01&toDate=2024-12-31
```

#### Balance Sheet

```bash
GET /xero/api.xro/2.0/Reports/BalanceSheet?date=2024-12-31
```

#### Trial Balance

```bash
GET /xero/api.xro/2.0/Reports/TrialBalance?date=2024-12-31
```

### Organisation

```bash
GET /xero/api.xro/2.0/Organisation
```

## Invoice Types

- `ACCREC` - Accounts Receivable (sales invoice)
- `ACCPAY` - Accounts Payable (bill)

## Code Examples

### JavaScript

```javascript
const response = await fetch(
  'https://gateway.maton.ai/xero/api.xro/2.0/Contacts',
  {
    headers: {
      'Authorization': `Bearer ${process.env.MATON_API_KEY}`
    }
  }
);
```

### Python

```python
import os
import requests

response = requests.get(
    'https://gateway.maton.ai/xero/api.xro/2.0/Contacts',
    headers={'Authorization': f'Bearer {os.environ["MATON_API_KEY"]}'}
)
```

## Notes

- `Xero-Tenant-Id` header is automatically injected
- Dates are in `YYYY-MM-DD` format
- Multiple records can be created in a single request using arrays
- Use `where` query parameter for filtering
- IMPORTANT: When using curl commands, use `curl -g` when URLs contain brackets (`fields[]`, `sort[]`, `records[]`) to disable glob parsing
- IMPORTANT: When piping curl output to `jq` or other commands, environment variables like `$MATON_API_KEY` may not expand correctly in some shell environments. You may get "Invalid API key" errors when piping.

## Error Handling

| Status | Meaning |
|--------|---------|
| 400 | Missing Xero connection |
| 401 | Invalid or missing Maton API key |
| 429 | Rate limited (10 req/sec per account) |
| 4xx/5xx | Passthrough error from Xero API |

### Troubleshooting: API Key Issues

1. Check that the `MATON_API_KEY` environment variable is set:

```bash
echo $MATON_API_KEY
```

2. Verify the API key is valid by listing connections:

```bash
python <<'EOF'
import urllib.request, os, json
req = urllib.request.Request('https://ctrl.maton.ai/connections')
req.add_header('Authorization', f'Bearer {os.environ["MATON_API_KEY"]}')
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=2))
EOF
```

### Troubleshooting: Invalid App Name

1. Ensure your URL path starts with `xero`. For example:

- Correct: `https://gateway.maton.ai/xero/api.xro/2.0/Contacts`
- Incorrect: `https://gateway.maton.ai/api.xro/2.0/Contacts`

## Resources

- [Xero API Overview](https://developer.xero.com/documentation/api/accounting/overview)
- [Contacts](https://developer.xero.com/documentation/api/accounting/contacts)
- [Invoices](https://developer.xero.com/documentation/api/accounting/invoices)
- [Accounts](https://developer.xero.com/documentation/api/accounting/accounts)
- [Payments](https://developer.xero.com/documentation/api/accounting/payments)
- [Reports](https://developer.xero.com/documentation/api/accounting/reports)
- [Maton Community](https://discord.com/invite/dBfFAcefs2)
- [Maton Support](mailto:support@maton.ai)
