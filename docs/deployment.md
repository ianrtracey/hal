# Deployment

Hal runs on a VPS accessible via `ssh openclaw` (`138.197.46.200`).
Public URL: `https://ians-hal.duckdns.org`

## Prerequisites

- VPS with SSH access (`ssh openclaw`)
- DuckDNS subdomain `ians-hal.duckdns.org` pointed at the VPS IP
- Blooio API key and Anthropic API key in `.env`

## 1. Install uv and dependencies

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
cd /root/hal
uv sync
```

## 2. Install Caddy (HTTPS reverse proxy)

Caddy auto-provisions Let's Encrypt TLS certificates.

```bash
apt update && apt install -y caddy
```

Configure it:

```bash
cat > /etc/caddy/Caddyfile << 'EOF'
ians-hal.duckdns.org {
    reverse_proxy localhost:8000
}
EOF
systemctl restart caddy
```

Verify:
```bash
curl https://ians-hal.duckdns.org/health
```

## 3. Create `.env` on VPS

```bash
cat > /root/hal/.env << 'EOF'
BLOOIO_API_KEY=<your key>
BLOOIO_WEBHOOK_SECRET=<from step 5>
ANTHROPIC_API_KEY=<your key>
ANTHROPIC_MODEL=claude-sonnet-4-20250514
HAL_WEBHOOK_TOKEN=<generate something>
HAL_ADMIN_TOKEN=<generate something>
HAL_DB_PATH=var/hal.sqlite3
HAL_SCHEDULER_ENABLED=1
HAL_AGENT_ENABLED=1
HAL_PROMPT_DIR=prompts
EOF
```

Or rsync it from local (included by default in the rsync command).

## 4. Start Hal

Create the data directory and start in a tmux session:

```bash
mkdir -p /root/hal/var/log
tmux new -s hal
cd /root/hal
uv run python supervisor.py
```

Detach with `Ctrl-b d`. Reattach with `tmux attach -t hal`.

Verify:
```bash
curl http://localhost:8000/health
```

## 5. Register Blooio webhook

```bash
curl -X POST 'https://backend.blooio.com/v2/api/webhooks' \
  -H "Authorization: Bearer $BLOOIO_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"webhook_url":"https://ians-hal.duckdns.org/webhooks/blooio","webhook_type":"message"}'
```

Save the returned `signing_secret` as `BLOOIO_WEBHOOK_SECRET` in `/root/hal/.env`, then restart the app.

Delete any old webhook registrations (e.g., ngrok URLs) if needed.

## 6. Verify end-to-end

```bash
# Health check
curl https://ians-hal.duckdns.org/health

# Watch logs
tail -f /root/hal/var/log/hal.log

# Send a test SMS and confirm Hal replies
```

## Deploying updates

From local:

```bash
rsync -avz ./ openclaw:/root/hal \
  --exclude .git \
  --exclude __pycache__ \
  --exclude .pytest_cache \
  --exclude var \
  --exclude .venv \
  --exclude .DS_Store
```

Then restart on the VPS (in the `hal` tmux session):
```bash
# Ctrl-C to stop supervisor, then:
cd /root/hal && uv run python supervisor.py
```

## Architecture

- **Caddy** listens on 443 (HTTPS), reverse-proxies to localhost:8000
- **supervisor.py** manages the Uvicorn process (auto-restart, health checks, rollback)
- **Uvicorn** runs the FastAPI app on 127.0.0.1:8000
- **SQLite** database in `var/hal.sqlite3`
- **Logs** in `var/log/hal.log`
- **DuckDNS** provides the domain name (`ians-hal.duckdns.org` → `138.197.46.200`)
