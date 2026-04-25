# Blooio Messaging API v2 - Quick Reference

OpenAPI spec: https://backend.blooio.com/v2/api/openapi.json

## Base URL
https://backend.blooio.com/v2/api

## Authentication
All requests require Bearer token authentication:
```
Authorization: Bearer YOUR_API_KEY
```

## Key Endpoints

### Send a Message
POST /chats/{chatId}/messages
- chatId: URL-encoded phone number (+15551234567 → %2B15551234567), email, or group ID (grp_xxx)
- Body: { "text": "Hello", "attachments": ["https://..."], "metadata": {} }
- Returns: { "message_id": "msg_xxx", "status": "queued" }

### Get Message Status
GET /chats/{chatId}/messages/{messageId}/status
- Returns: { "message_id": "...", "status": "queued|sent|delivered|failed", "protocol": "imessage|sms" }

### List Contacts
GET /contacts?limit=50&offset=0&q=search&sort=recent
- Returns: { "contacts": [...], "pagination": { "limit", "offset", "total" } }

### Create Contact
POST /contacts
- Body: { "identifier": "+15551234567", "name": "John Doe" }

### Check Contact Capabilities
GET /contacts/{contactId}/capabilities
- Returns: { "capabilities": { "imessage": true, "sms": true } }

### List Groups
GET /groups?limit=50&offset=0

### Create Group
POST /groups
- Body: { "name": "Team Chat", "members": ["+15551234567", "+15559876543"] }

### List Webhooks
GET /webhooks

### Create Webhook
POST /webhooks
- Body: { "webhook_url": "https://...", "webhook_type": "message|status|all" }
- Returns includes signing_secret (shown once!)

### Add Reaction
POST /chats/{chatId}/messages/{messageId}/reactions
- Body: { "reaction": "+love" } (or +like, +dislike, +laugh, +emphasize, +question)
- Use "-love" to remove a reaction

### Start Typing Indicator
POST /chats/{chatId}/typing

### Mark Chat as Read
POST /chats/{chatId}/read

## Webhook Events
Your webhook receives POST requests with X-Blooio-Event header:
- message.received: Inbound message
- message.sent: Outbound message sent
- message.delivered: Message delivered
- message.failed: Delivery failed
- message.read: Message was read (iMessage only)
- message.reaction: Tapback reaction added or removed (includes reaction type, action, sender, and original message text)

## Common Patterns

### URL Encoding Phone Numbers
Phone numbers in paths must be URL-encoded:
- +15551234567 → %2B15551234567
- JavaScript: encodeURIComponent('+15551234567')
- Python: urllib.parse.quote('+15551234567', safe='')

### Idempotency
Use Idempotency-Key header for safe retries:
```
Idempotency-Key: unique-request-id
```

### Error Format
All errors return: { "error": "code", "message": "description", "status": 400 }

## Status Codes
- 200: Success
- 201: Created
- 202: Accepted (message queued)
- 400: Bad request
- 401: Unauthorized
- 403: Forbidden
- 404: Not found
- 409: Conflict
- 503: No active number available
