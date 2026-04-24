# Hal

## Deployment

The app runs on a VPS accessible via `ssh openclaw`.

Deploy with rsync:

```bash
rsync -avz ./ openclaw:/root/hal --exclude .git --exclude __pycache__ --exclude .pytest_cache --exclude var
```
