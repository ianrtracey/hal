# Hal

Local personal AI assistant with SMS ingress through Blooio, Claude-backed replies,
SQLite persistence, scheduled jobs, and a small external supervisor for restarts.

## Design docs

- [Agent harness technical spec](docs/agent-harness-tech-spec.md)

## Run the app

```bash
uv run uvicorn hal.app:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Claude Code harness

V0 harness mode runs a fresh Claude Code turn for each inbound SMS, using chat
history from SQLite and prompt files from `prompts/`.

Enable it with:

```bash
HAL_AGENT_ENABLED=1
HAL_CLAUDE_COMMAND=claude
```

Local prompt iteration without a webhook:

```bash
uv run python -m hal.cli simulate-inbound --chat-id '+15551234567' --text 'hello'
```

Claude is instructed to reply through:

```bash
uv run python -m hal.cli send-sms --chat-id '+15551234567' --text '...'
```

Blooio webhook endpoint:

```text
POST /webhooks/blooio
```

Set `BLOOIO_WEBHOOK_SECRET` to verify Blooio's `X-Blooio-Signature`
header before processing messages. If no signing secret is configured,
`HAL_WEBHOOK_TOKEN` can be used as a local fallback via `X-Hal-Webhook-Token`
or `?token=...`.

### Local development with ngrok

Start the app and expose it via ngrok so Blooio can reach your local machine:

```bash
# Terminal 1 — run the app
uv run uvicorn hal.app:app --host 127.0.0.1 --port 8000

# Terminal 2 — start the ngrok tunnel
ngrok http 8000
```

ngrok will print a public URL like `https://xxxx-xxxx.ngrok-free.app`. The ngrok
inspect UI is available at `http://127.0.0.1:4040` to replay and debug requests.

Register the public HTTPS URL with Blooio:

```bash
curl -X POST 'https://backend.blooio.com/v2/api/webhooks' \
  -H "Authorization: Bearer $BLOOIO_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"webhook_url":"https://YOUR_NGROK_DOMAIN/webhooks/blooio","webhook_type":"message"}'
```

Save the returned `signing_secret` as `BLOOIO_WEBHOOK_SECRET` in `.env` and
restart Hal.

## Run under the supervisor

```bash
uv run python supervisor.py
```

The supervisor starts Uvicorn, writes process output to `var/log/hal.log`, watches
for `var/restart.request`, and checks `/health` after restarts. If health fails
after a restart, it runs:

```bash
git revert --no-edit HEAD
```

That assumes self-modifications are committed one change at a time before restart.

## Self-modification guardrails

The app currently treats these paths as protected:

- `.env`
- `.git`
- `.venv`
- `supervisor.py`
- `var/`

`POST /admin/validate-edit` checks proposed edit paths against those guardrails
and runs a Python compile validation. If `HAL_ADMIN_TOKEN` is set, pass it as
`X-Hal-Admin-Token` or `?token=...`.

## Tests

```bash
uv run pytest
```
