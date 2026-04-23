#!/bin/bash
export PYTHONUNBUFFERED=1

# Re-register Telegram + MoySklad webhooks (safe to run every deploy)
echo "=== Setting up webhooks ==="
python -u manage.py setup_webhooks
echo "=== Webhooks done ==="

# Run sync_transactions in foreground first so we can see all output/errors
echo "=== Starting sync_transactions ==="
python -u manage.py sync_transactions
echo "=== sync_transactions finished ==="

# Start the web server
exec gunicorn -c gunicorn.conf.py moysklad_bot.wsgi:application
