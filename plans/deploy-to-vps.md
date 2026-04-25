# Deploy Hal to VPS

## Overview

Deploy Hal to the VPS (`ssh openclaw` / `138.197.46.200`) so it runs permanently and receives Blooio webhooks over HTTPS — replacing the current ngrok-based local dev setup.

## Current State

- App runs locally with `uv run uvicorn hal.app:app`
- Blooio webhooks pointed at ngrok tunnel
- Supervisor exists (`supervisor.py`) but only used locally
- rsync command in CLAUDE.md exists but is incomplete (missing `.venv`, `.env` excludes)

## Steps

### 1. Check VPS prerequisites (manual — SSH needs hardware key)

SSH in and verify:
```bash
ssh openclaw
python3 --version   # need 3.12+
which uv            # need uv installed
```

If missing:
```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Python 3.12 — uv can manage this, or install via apt
apt update && apt install -y python3.12 python3.12-venv
```

### 2. rsync code to VPS

Update the rsync command to exclude `.venv` and `.env`:
```bash
rsync -avz ./ openclaw:/root/hal \
  --exclude .git \
  --exclude __pycache__ \
  --exclude .pytest_cache \
  --exclude var \
  --exclude .venv \
  --exclude .env \
  --exclude .DS_Store
```

### 3. Install dependencies on VPS

```bash
ssh openclaw
cd /root/hal
uv sync
```

This creates a `.venv` on the VPS with the correct platform binaries.

### 4. Create production `.env` on VPS

```bash
cat > /root/hal/.env << 'EOF'
BLOOIO_API_KEY=<same key>
BLOOIO_WEBHOOK_SECRET=<will get new one when registering webhook>
ANTHROPIC_API_KEY=<same key>
ANTHROPIC_MODEL=claude-sonnet-4-20250514
HAL_WEBHOOK_TOKEN=<generate something>
HAL_ADMIN_TOKEN=<generate something>
HAL_DB_PATH=var/hal.sqlite3
HAL_SCHEDULER_ENABLED=1
HAL_AGENT_ENABLED=1
HAL_CLAUDE_COMMAND=claude
HAL_CLAUDE_TIMEOUT_SECONDS=120
HAL_PROMPT_DIR=prompts
EOF
```

### 5. Set up reverse proxy for HTTPS

Blooio requires HTTPS for webhook delivery. Two options:

**Option A: Caddy (recommended — automatic HTTPS with Let's Encrypt)**

Requires a domain name pointed at the VPS IP. If you have one:
```bash
apt install -y caddy
```

Caddyfile (`/etc/caddy/Caddyfile`):
```
hal.yourdomain.com {
    reverse_proxy localhost:8000
}
```

Caddy automatically provisions and renews TLS certificates.

**Option B: Nginx + certbot**

More manual but works if Caddy isn't an option:
```bash
apt install -y nginx certbot python3-certbot-nginx
```

Then configure the site and run `certbot --nginx`.

**Option C: No domain — direct IP with self-signed or Blooio token auth only**

If Blooio accepts plain HTTP webhooks (unlikely) or you want to use the token-based fallback (`?token=...`) without signature verification. Not recommended for production.

**Decision needed:** Do you have a domain name to point at this VPS?

### 6. Create systemd service

Create `/etc/systemd/system/hal.service`:
```ini
[Unit]
Description=Hal AI Assistant (supervisor)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/hal
ExecStart=/root/hal/.venv/bin/python supervisor.py
Restart=on-failure
RestartSec=5
EnvironmentFile=/root/hal/.env

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
systemctl daemon-reload
systemctl enable hal
systemctl start hal
systemctl status hal
```

### 7. Create `var/` directory on VPS

```bash
mkdir -p /root/hal/var/log
```

### 8. Register Blooio webhook with VPS URL

Once HTTPS is working, register the new webhook URL:
```bash
curl -X POST 'https://backend.blooio.com/v2/api/webhooks' \
  -H "Authorization: Bearer $BLOOIO_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"webhook_url":"https://hal.yourdomain.com/webhooks/blooio","webhook_type":"message"}'
```

Save the returned `signing_secret` as `BLOOIO_WEBHOOK_SECRET` in the VPS `.env`, then restart:
```bash
systemctl restart hal
```

You may also need to delete the old ngrok webhook registration.

### 9. Verify

```bash
# Health check
curl https://hal.yourdomain.com/health

# Check logs
ssh openclaw 'tail -f /root/hal/var/log/hal.log'

# Send a test SMS and watch it come through
```

## Code Changes Needed

**The webhook handler (`hal/app.py`) does NOT need changes.** It already handles both Blooio signature verification and token-based fallback. The FastAPI app works correctly regardless of whether it's behind ngrok or a reverse proxy.

**Update CLAUDE.md** rsync command to include the additional excludes (`.venv`, `.env`, `.DS_Store`).

## Open Questions

1. **Domain name?** Do you have one to point at `138.197.46.200`? This determines the HTTPS approach.
2. **Deregister old webhook?** Need to check if the ngrok webhook registration should be removed first, or if Blooio allows multiple.
3. **Claude CLI on VPS?** If `HAL_AGENT_ENABLED=1`, the agent uses the OpenAI Agents SDK (not the Claude CLI), so the CLI isn't needed. But `HAL_CLAUDE_COMMAND` is still in config — confirm this isn't used in the agent path.
4. **VPS Python version?** Need to verify 3.12+ is available.
