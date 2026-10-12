"""Django settings for the CDK Instructor Internet Access and Bandwidth Control System."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    MIKROTIK_MOCK=(bool, False),
    MIKROTIK_PORT=(int, 8728),
    MIKROTIK_TIMEOUT=(float, 5.0),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "192.168.88.5"])

# --- Portal / router settings (all from .env) -------------------------------------------
PORTAL_BASE_URL = env("PORTAL_BASE_URL", default="http://192.168.88.5:8000")
CSRF_TRUSTED_ORIGINS = [PORTAL_BASE_URL]

MIKROTIK_HOST = env("MIKROTIK_HOST", default="192.168.88.1")
MIKROTIK_PORT = env("MIKROTIK_PORT")
MIKROTIK_USER = env("MIKROTIK_USER", default="djangoapi")
MIKROTIK_PASSWORD = env("MIKROTIK_PASSWORD", default="")
MIKROTIK_HOTSPOT_SERVER = env("MIKROTIK_HOTSPOT_SERVER", default="hotspot1")
MIKROTIK_WAN_INTERFACE = env("MIKROTIK_WAN_INTERFACE", default="ether1")
MIKROTIK_TIMEOUT = env("MIKROTIK_TIMEOUT")
MIKROTIK_MOCK = env("MIKROTIK_MOCK")
# Mock router keeps its state in this file so runserver and sync_sessions share it.
MIKROTIK_MOCK_STATE_FILE = BASE_DIR / ".mock_router.json"

SEED_ADMIN_PASSWORD = env("SEED_ADMIN_PASSWORD", default="admin12345")

# Login rate limiting: N failed attempts per (username, ip) -> lockout.
LOGIN_MAX_FAILURES = 5
LOGIN_LOCKOUT_MINUTES = 5

# --- Application definition --------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "academics",
    "instructors",
    "network",
    "adminpanel",
    "portal",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.ForcePasswordChangeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "academics.context.active_semester",
                "instructors.context.pending_registrations",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite for now; switch to PostgreSQL by setting DATABASE_URL in .env, e.g.
#   DATABASE_URL=postgres://user:password@localhost:5432/cdk_portal
DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",  # default
    "accounts.hashers.BulkPBKDF2PasswordHasher",         # fast, masterlist import only
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
SESSION_COOKIE_AGE = 60 * 60 * 12

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {message_constants.ERROR: "danger"}
