"""Réglages de test : SQLite en mémoire, aucune dépendance au Postgres partagé.

    python manage.py test --settings=config.settings_test

Aucune fonctionnalité spécifique à PostgreSQL n'est utilisée par l'app, d'où
le choix de ne pas créer de base de test sur l'instance partagée du lab.
"""
from .settings import *  # noqa: F401,F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}

KEYCLOAK_CLIENT_ID = 'peripheriques'
KEYCLOAK_REQUIRED_GROUPS = 'developers,famille,amis'
PERIPHERIQUES_ADMIN_GROUPS = 'developers'
MEDIA_PUBLISH_USER = 'publisher'
MEDIA_PUBLISH_PASSWORD = 'pub-secret'
MEDIA_INTERNAL_USER = 'backend'
MEDIA_INTERNAL_PASSWORD = 'int-secret'
MEDIA_HOOK_KEY = 'hook-key-for-tests'
MEDIA_API_URL = 'http://media-test:9997'
MEDIA_PLAYBACK_URL = 'http://media-test:9996'
FORCE_SCRIPT_NAME = '/peripheriques-api'
LOGGING = {'version': 1, 'disable_existing_loggers': False}
