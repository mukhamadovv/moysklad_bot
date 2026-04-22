import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

# Load .env file if present (dev convenience — in production set env vars directly)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True, interpolate=False)
except ImportError:
    pass  # python-dotenv not installed — rely on real env vars


def _env(key: str, default=None, required: bool = False) -> str:
    value = os.environ.get(key, default)
    if required and not value:
        raise ImproperlyConfigured(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example → .env and fill in all values."
        )
    return value


BASE_DIR = Path(__file__).resolve().parent.parent

# ─── Core ────────────────────────────────────────────────────────────────────
SECRET_KEY = _env("DJANGO_SECRET_KEY", required=True)

DEBUG = _env("DEBUG", "False") == "True"

_raw_hosts = _env("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [h.strip() for h in _raw_hosts.replace(",", " ").split() if h.strip()]

# Automatically trust Railway's public domain if available
_railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
if _railway_domain and _railway_domain not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_railway_domain)

if not ALLOWED_HOSTS:
    if DEBUG:
        ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
    else:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be set in production. "
            "Example: ALLOWED_HOSTS=yourdomain.com"
        )

# ─── Apps ────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'bot',
    'webhook',
]

# ─── Middleware ───────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'moysklad_bot.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'moysklad_bot.wsgi.application'

# ─── Database ────────────────────────────────────────────────────────────────
_db_url = _env("DATABASE_URL", "")
if _db_url and ("postgresql" in _db_url or "postgres" in _db_url):
    import re
    # Handles both postgresql:// and postgres:// schemes Railway may provide
    _m = re.match(r'postgres(?:ql)?(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):?(\d+)?/([^?]+)', _db_url)
    if _m:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.postgresql',
                'USER': _m.group(1),
                'PASSWORD': _m.group(2),
                'HOST': _m.group(3),
                'PORT': _m.group(4) or '5432',
                'NAME': _m.group(5),
                'CONN_MAX_AGE': 60,
            }
        }
    else:
        raise ImproperlyConfigured(f"Could not parse DATABASE_URL: {_db_url}")
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ─── Static files ────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Security (applied when DEBUG=False) ─────────────────────────────────────
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = False      # Let nginx handle redirects
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

# ─── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", required=True)
TELEGRAM_WEBHOOK_URL = _env("TELEGRAM_WEBHOOK_URL", required=True)

# ─── MoySklad ────────────────────────────────────────────────────────────────
# Change only MOYSKLAD_TOKEN in .env to switch to a different MoySklad account
MOYSKLAD_TOKEN = _env("MOYSKLAD_TOKEN", required=True)
MOYSKLAD_WEBHOOK_SECRET = _env("MOYSKLAD_WEBHOOK_SECRET", required=True)

# ─── Business logic ──────────────────────────────────────────────────────────
BONUS_PERCENT = int(_env("BONUS_PERCENT", "3"))

# ─── Logging ─────────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'bot':     {'handlers': ['console'], 'level': 'INFO' if not DEBUG else 'DEBUG', 'propagate': False},
        'webhook': {'handlers': ['console'], 'level': 'INFO' if not DEBUG else 'DEBUG', 'propagate': False},
        'django':  {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}
