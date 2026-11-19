"""
SQLite settings for local unit tests when remote MySQL cannot CREATE DATABASE.

Usage:
  python manage.py test support --settings=patrol_backend.test_settings
"""

from .settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Avoid Redis / Celery side effects during tests
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Skip expensive face-index warmup if any AppConfig hooks into ready()
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# Avoid support write throttles interfering with suite volume
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "support_create": "10000/hour",
        "support_comment": "10000/hour",
        "support_attachment": "10000/hour",
        "support_reopen": "10000/hour",
    },
}

# Capture support emails in django.core.mail.outbox during tests
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
