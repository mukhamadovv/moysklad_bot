import multiprocessing
import os

# Workers: 2-4 × CPU cores is a common rule for I/O-bound apps
workers = multiprocessing.cpu_count() * 2 + 1

# Use sync worker (default) — fine for webhook bots
worker_class = "sync"

# Railway injects PORT env var; fall back to 8000 locally
bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"

# Timeouts
timeout = 30
keepalive = 5

# Logging
accesslog = "-"       # stdout
errorlog = "-"        # stderr
loglevel = "info"

# Restart workers after this many requests (prevents memory leaks)
max_requests = 1000
max_requests_jitter = 100

# Security: trust Railway's proxy
forwarded_allow_ips = "*"
