#!/usr/bin/env python
import os
import sys


def main():
    # Load .env file if it exists (for local development)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'moysklad_bot.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Django is not installed.") from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
