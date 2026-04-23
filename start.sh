#!/bin/bash
# Run sync_transactions in the background so gunicorn starts immediately
python manage.py sync_transactions &

# Start the web server
exec gunicorn -c gunicorn.conf.py moysklad_bot.wsgi:application
