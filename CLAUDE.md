# Hal

## Environment

You are running directly on the VPS that hosts Hal. The working directory
(`/root/hal`) **is** the live deployment — no rsync, no separate
local/remote sync step. Edits to files here are edits to production once
the supervisor restarts.

- Host: `openclaw` (`138.197.46.200`)
- Public URL: `https://ians-hal.duckdns.org`
- Repo / live tree: `/root/hal`
- The supervisor runs in a tmux session named `hal`.

## Development workflow

1. Edit files in `/root/hal` directly.
2. Run tests: `uv run pytest`.
3. If you added or changed dependencies, run `uv sync`.
4. Restart the supervisor to pick up code changes (see below).

### Restarting the supervisor

The supervisor runs in the `hal` tmux session. To restart it:

```bash
# Attach:
tmux attach -t hal
# Then inside the session: Ctrl-C to stop, then:
cd /root/hal && uv run python supervisor.py
# Detach with Ctrl-b d
```

Or, send the keys without attaching:

```bash
tmux send-keys -t hal C-c
# wait a moment for clean shutdown
tmux send-keys -t hal "cd /root/hal && uv run python supervisor.py" Enter
```

Restart only when you've changed code the supervisor loads at startup
(the FastAPI app, agent runner, scheduler, prompts loaded once, etc.).
For prompt-file edits read each turn, no restart needed.

### Full setup (first-time)

See [docs/deployment.md](docs/deployment.md) for complete VPS setup
instructions.
