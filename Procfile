web: python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn -c gunicorn.conf.py moysklad_bot.wsgi:application
