# Hal

## Deployment

The app runs on a VPS accessible via `ssh openclaw` (`138.197.46.200`).
Public URL: `https://ians-hal.duckdns.org`

### Quick deploy (rsync)

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

### Full setup (first-time)

See [docs/deployment.md](docs/deployment.md) for complete VPS setup instructions.
