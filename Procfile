web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && python manage.py sync_transactions && gunicorn -c gunicorn.conf.py moysklad_bot.wsgi:application
