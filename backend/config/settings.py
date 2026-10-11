"""
Settings pour Peripheriques.
Les variables sensibles sont lues depuis le fichier .env via python-decouple.
"""
from decouple import config

# ── Sécurité ──────────────────────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY', default='django-insecure-peripheriques-change-in-production')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='*').split(',')

# ── Reverse proxy (Caddy) ──────────────────────────────────────────────────────
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
FORCE_SCRIPT_NAME = config('SCRIPT_NAME', default='')

import os
# ── Applications ──────────────────────────────────────────────────────────────
# ── Répertoires de templates ──────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'rest_framework',
    'corsheaders',
    'drf_spectacular_sidecar',
    'drf_spectacular',
    'api',
]

# ── Middleware ─────────────────────────────────────────────────────────────────
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'

# ── Base de données ────────────────────────────────────────────────────────────
_DB_SCHEMA = config('DB_SCHEMA', default='peripheriques')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'HOST':     config('DB_HOST',     default='postgres'),
        'PORT':     config('DB_PORT',     default=5432, cast=int),
        'NAME':     config('DB_NAME',     default='devdb'),
        'USER':     config('DB_USER',     default='devuser'),
        'PASSWORD': config('DB_PASSWORD', default='devpassword'),
        'OPTIONS': {
            # Isole les tables dans le schéma dédié à cette application.
            'options': f'-c search_path={_DB_SCHEMA},public',
        },
    }
}

DB_SCHEMA = _DB_SCHEMA
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
USE_TZ = True

# ── Django REST Framework ──────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'api.authentication.KeycloakJWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# ── Keycloak ───────────────────────────────────────────────────────────────────
KEYCLOAK_ISSUER_URI = config(
    'KEYCLOAK_ISSUER_URI',
    default='http://keycloak:8080/realms/ssolab',
)
KEYCLOAK_CLIENT_ID = config('KEYCLOAK_CLIENT_ID', default='swagger-ui')
# Groupes autorisés à utiliser l'API, séparés par des virgules. Vide ⇒ toute
# personne authentifiée sur ce client passe. Renseigné par create-app-client.sh
# à partir de --require-group.
KEYCLOAK_REQUIRED_GROUPS = config('KEYCLOAK_REQUIRED_GROUPS', default='')
# Prefer building the public issuer from KEYCLOAK_PUBLIC_URL + KEYCLOAK_REALM
# (so we don't introduce a separate KEYCLOAK_PUBLIC_ISSUER_URI variable).
KEYCLOAK_PUBLIC_URL = config('KEYCLOAK_PUBLIC_URL', default=None)
KEYCLOAK_REALM = config('KEYCLOAK_REALM', default=None)

# Construct the issuer URL for the Swagger UI. If both public URL and realm are
# provided, use them to form the WAN-facing issuer (e.g. https://host:port/realms/realm).
# Otherwise fall back to the internal KEYCLOAK_ISSUER_URI value.
if KEYCLOAK_PUBLIC_URL and KEYCLOAK_REALM:
    _KEYCLOAK_ISSUER_FOR_UI = f"{KEYCLOAK_PUBLIC_URL.rstrip('/')}/realms/{KEYCLOAK_REALM}"
else:
    _KEYCLOAK_ISSUER_FOR_UI = KEYCLOAK_ISSUER_URI

SPECTACULAR_SETTINGS = {
    'TITLE': 'Peripheriques API',
    'DESCRIPTION': 'Documentation interactive OpenAPI/Swagger',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SECURITY': [{'BearerAuth': []}],
    'COMPONENTS': {
        'securitySchemes': {
            'BearerAuth': {
                'type': 'oauth2',
                'flows': {
                    'authorizationCode': {
                        'authorizationUrl': f'{_KEYCLOAK_ISSUER_FOR_UI}/protocol/openid-connect/auth',
                        'tokenUrl': f'{_KEYCLOAK_ISSUER_FOR_UI}/protocol/openid-connect/token',
                        'scopes': {
                            'openid': 'OpenID Connect scope',
                            'profile': 'Profile scope',
                            'email': 'Email scope',
                        },
                    }
                }
            }
        }
    },
    # Swagger UI servi localement (paquet drf-spectacular-sidecar, route
    # api/docs/sidecar/ de config/urls.py) — jamais un CDN en `@latest`.
    'SWAGGER_UI_DIST': f'{FORCE_SCRIPT_NAME}/api/docs/sidecar',
    'SWAGGER_UI_FAVICON_HREF': f'{FORCE_SCRIPT_NAME}/api/docs/sidecar/favicon-32x32.png',
    'SWAGGER_UI_OAUTH2_CONFIG': {
        'clientId': KEYCLOAK_CLIENT_ID,
        'usePkceWithAuthorizationCodeGrant': True,
        'scope': 'openid profile email',
        'authorizationUrl': f'{_KEYCLOAK_ISSUER_FOR_UI}/protocol/openid-connect/auth',
        'tokenUrl': f'{_KEYCLOAK_ISSUER_FOR_UI}/protocol/openid-connect/token',
        'oauth2RedirectUrl': f'{FORCE_SCRIPT_NAME}/api/docs/oauth2-redirect.html',
    },
    'POSTPROCESSING_HOOKS': [
        'config.spectacular_hooks.add_bearer_security',
    ],
}

# ── CORS ───────────────────────────────────────────────────────────────────────
# En développement (DEBUG=True), toutes les origines sont autorisées.
# En production, les origines sont dérivées automatiquement depuis SERVER_URL_WAN/LAN + PORT_FRONTEND,
# ou surchargées via CORS_ALLOWED_ORIGINS (comma-separated).
CORS_ALLOW_ALL_ORIGINS = DEBUG

if not DEBUG:
    _cors_explicit = config('CORS_ALLOWED_ORIGINS', default='')
    _cors_list = [s for s in _cors_explicit.split(',') if s]
    if not _cors_list:
        _fport = config('PORT_FRONTEND', default='')
        _wan = config('SERVER_URL_WAN', default='')
        _lan = config('SERVER_URL_LAN', default='')
        _local = config('FRONTEND_URL', default='')
        for _o in [_local,
                   f"{_wan}:{_fport}" if _wan and _fport else '',
                   f"{_lan}:{_fport}" if _lan and _fport else '']:
            if _o and _o not in _cors_list:
                _cors_list.append(_o)
    CORS_ALLOWED_ORIGINS = _cors_list
else:
    CORS_ALLOWED_ORIGINS = []

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ── Périphériques / lab-media ──────────────────────────────────────────────────
TIME_ZONE = 'UTC'
# Fuseau dans lequel s'entend le paramètre ?date= de /api/recordings/ (I3).
RECORDINGS_TZ = 'Europe/Paris'

# Groupes qui GÈRENT les droits (contrat v3 : aucun accès implicite aux flux).
PERIPHERIQUES_ADMIN_GROUPS = config('PERIPHERIQUES_ADMIN_GROUPS', default='developers')

# Brique lab-media (lab-media/API.md). Le backend ne parle plus à MediaMTX.
#  · MEDIA_API_URL : API de contrôle de lab-media-auth (dev-net). État des
#    chemins, sessions, expulsion ET relecture : une seule base.
#  · MEDIA_INTERNAL_USER / _PASSWORD : identifiants de CONTRÔLE de l'app auprès de
#    lab-media (= LAB_MEDIA_APP_PERIPHERIQUES_CONTROL_USER / _PASSWORD dans
#    lab-media/.env). Ils ne donnent accès qu'aux chemins `webcam/*`.
#  · MEDIA_HOOK_KEY : clé que lab-media présente à notre hook de lecture
#    /api/media-auth/?key=… (= LAB_MEDIA_APP_PERIPHERIQUES_HOOK_KEY). Vide ⇒ le
#    hook refuse tout.
MEDIA_API_URL = config('MEDIA_API_URL', default='http://lab-media-auth:9100')
MEDIA_INTERNAL_USER = config('MEDIA_INTERNAL_USER', default='')
MEDIA_INTERNAL_PASSWORD = config('MEDIA_INTERNAL_PASSWORD', default='')
MEDIA_HOOK_KEY = config('MEDIA_HOOK_KEY', default='')

# Compte de service peripheriques-admin (view-users/query-users) : vérifie
# qu'un email existe dans le realm avant d'accorder un droit.
KEYCLOAK_ADMIN_CLIENT_ID = config('KEYCLOAK_ADMIN_CLIENT_ID', default='')
KEYCLOAK_ADMIN_CLIENT_SECRET = config('KEYCLOAK_ADMIN_CLIENT_SECRET', default='')

# Durée de validité d'un ticket de relecture (I3, /api/recordings/stream/).
RECORDING_TICKET_MAX_AGE = 300

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    # v5 : masque `?key=` / `?ticket=` (cf. api/log_filters.py, qui pose aussi
    # ce filtre sur django.server / gunicorn.access au démarrage).
    'filters': {'redact': {'()': 'api.log_filters.RedactSecretsFilter'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'filters': ['redact']}},
    'loggers': {'api': {'handlers': ['console'], 'level': 'INFO'}},
}

# Catalogue « Matériel conseillé » (contrat I7, v6) — servi tel quel aux admins
# par /api/admin/materiel/, relu à chaque requête.
MATERIEL_PATH = os.path.join(BASE_DIR, 'api', 'data', 'materiel.json')
