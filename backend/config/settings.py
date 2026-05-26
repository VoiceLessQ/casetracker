"""
Django settings for the CaseTracker prototype.

PROTOTYPE / PLACEHOLDER ONLY. SECRET_KEY and DEBUG below are dev defaults.
Set SECRET_KEY from the environment and DEBUG=False before this is ever
exposed anywhere real. This build is meant for synthetic data only.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "org",
    "people",
    "cases",
    "testing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must come AFTER AuthenticationMiddleware: it swaps request.user for a
    # superuser-chosen target while impersonating (testing tool).
    "testing.middleware.ImpersonationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

# i18n plumbing in from the start so the Kalaallisut pass is later just
# translation files, not a rewrite. Wrap user-facing strings in gettext.
LANGUAGE_CODE = "da"
LANGUAGES = [("da", "Dansk"), ("kl", "Kalaallisut"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "America/Nuuk"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Where the municipality's drive lives. Prototype: a local folder. Real
# deployment: the share / cloud path. The system links to files here; it does
# not store them. (No DRIVE_FOLDER_PEPPER needed — folders are keyed on each
# person's permanent internal uid, not on the CPR.)
MUNICIPAL_DRIVE_ROOT = os.environ.get("MUNICIPAL_DRIVE_ROOT", str(BASE_DIR / "drive"))

# Key for at-rest field encryption (e.g. the CPR). A stolen DB shows only
# ciphertext for encrypted columns because the key lives here, not in the data.
# OPERATIONAL WARNING: this key is required to read encrypted fields — LOSE IT
# AND THE CPRs ARE UNRECOVERABLE. In production set it from a secret manager /
# KMS, never commit a real key, and back it up separately from the database.
FIELD_ENCRYPTION_KEY = os.environ.get(
    "FIELD_ENCRYPTION_KEY", "dev-only-insecure-field-key-change-me"
)

# Optional dedicated key for encrypted database backups (manage.py backup_db).
# If empty, the backup key is derived from FIELD_ENCRYPTION_KEY. Set it (from a
# secret manager) to keep backup access separate from field-decryption access.
BACKUP_ENCRYPTION_KEY = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
