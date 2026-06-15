# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
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
DEBUG = os.environ.get("DEBUG", "0") == "1"
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
    "django.middleware.locale.LocaleMiddleware",   # language from session/cookie/Accept-Language
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
# Default language; a deployer in any country sets their own. Users can switch
# at runtime (LocaleMiddleware + the i18n/setlang view).
LANGUAGE_CODE = os.environ.get("LANGUAGE_CODE", "da")
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

def _read_secret(env_name, default=""):
    """Resolve a secret from <ENV>_FILE first (a path — so the key can live in a
    mounted secret / a file on the deployer's own network), else <ENV>, else the
    dev default. Lets keys live off the app host without code changes."""
    path = os.environ.get(env_name + "_FILE")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    return os.environ.get(env_name, default)


# Key for at-rest field encryption (e.g. the CPR). A stolen DB shows only
# ciphertext for encrypted columns because the key lives here, not in the data.
# OPERATIONAL WARNING: this key is required to read encrypted fields — LOSE IT
# AND THE CPRs ARE UNRECOVERABLE. In production set it from a secret manager /
# KMS / mounted file (FIELD_ENCRYPTION_KEY_FILE), never commit a real key, and
# back it up separately from the database.
FIELD_ENCRYPTION_KEY = _read_secret("FIELD_ENCRYPTION_KEY", "dev-only-insecure-field-key-change-me")

# Optional dedicated key for encrypted database backups (manage.py backup_db).
# If empty, the backup key is derived from FIELD_ENCRYPTION_KEY. Set it (env or
# BACKUP_ENCRYPTION_KEY_FILE) to keep backup access separate from field access.
BACKUP_ENCRYPTION_KEY = _read_secret("BACKUP_ENCRYPTION_KEY", "")

# Fail closed: the insecure dev defaults above must never back a non-DEBUG run
# (i.e. anything exposed). With DEBUG off, the real keys are required from the
# environment; local dev still runs on the defaults with DEBUG=1.
if not DEBUG:
    from django.core.exceptions import ImproperlyConfigured

    _insecure_defaults = {
        "SECRET_KEY": (SECRET_KEY, "dev-only-insecure-key-change-me"),
        "FIELD_ENCRYPTION_KEY": (FIELD_ENCRYPTION_KEY, "dev-only-insecure-field-key-change-me"),
    }
    _still_default = [name for name, (value, default) in _insecure_defaults.items() if value == default]
    if _still_default:
        raise ImproperlyConfigured(
            "Refusing to start with DEBUG off and the insecure dev default still set for: "
            + ", ".join(_still_default)
            + ". Set these from the environment (or a *_FILE secret) before running exposed."
        )

    # HTTPS hardening for any exposed (non-DEBUG) run. Assumes TLS is terminated
    # in front of the app; behind a reverse proxy you may also need
    # SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https").
    SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "1") == "1"
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000"))  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Bring-your-own-encryption: dotted path to a people.crypto.CryptoProvider
# subclass (your KMS/HSM/cipher). Empty = the built-in Fernet+HKDF base.
# Switching providers does NOT re-encrypt existing data — that needs a migration.
FIELD_ENCRYPTION_BACKEND = os.environ.get("FIELD_ENCRYPTION_BACKEND", "")

# Journal number format. Placeholders: {ref} (the case number) and {seq} (the
# running sequence within the case). Default is a per-case sequence, the most
# portable scheme; override for a global/yearly register convention.
JOURNAL_NUMBER_FORMAT = os.environ.get("JOURNAL_NUMBER_FORMAT", "{ref}-{seq:03d}")

# Cap on documents per encrypted export — squeezes the "select all → export"
# exfiltration channel. Larger pulls must be deliberately narrowed.
EXPORT_MAX_DOCUMENTS = int(os.environ.get("EXPORT_MAX_DOCUMENTS", "50"))
