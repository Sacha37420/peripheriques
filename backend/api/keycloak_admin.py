"""Client minimal de l'API Admin Keycloak (compte de service peripheriques-admin,
rôles view-users/query-users). Porté de lab-admin/backend/api/keycloak_admin.py
(même mécanique de token), réduit à la lecture : cette app ne crée aucun compte.
"""
from __future__ import annotations

import time

import requests
from django.conf import settings

_TIMEOUT = 10


class KeycloakAdminError(RuntimeError):
    """Erreur de communication ou de configuration avec l'API Admin Keycloak."""


def _base_realm() -> tuple[str, str]:
    issuer = (settings.KEYCLOAK_ISSUER_URI or '').rstrip('/')
    if '/realms/' not in issuer:
        raise KeycloakAdminError(f'KEYCLOAK_ISSUER_URI invalide : {issuer!r}')
    base, realm = issuer.split('/realms/', 1)
    return base, realm.split('/', 1)[0]


_token_cache: dict[str, float | str] = {'value': '', 'exp': 0.0}


def _token() -> str:
    now = time.time()
    if _token_cache['value'] and float(_token_cache['exp']) > now + 5:
        return str(_token_cache['value'])
    client_id = settings.KEYCLOAK_ADMIN_CLIENT_ID
    secret = settings.KEYCLOAK_ADMIN_CLIENT_SECRET
    if not client_id or not secret:
        raise KeycloakAdminError('KEYCLOAK_ADMIN_CLIENT_ID / _SECRET manquants.')
    base, realm = _base_realm()
    try:
        resp = requests.post(
            f'{base}/realms/{realm}/protocol/openid-connect/token',
            data={'grant_type': 'client_credentials', 'client_id': client_id,
                  'client_secret': secret},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise KeycloakAdminError(f'Keycloak injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise KeycloakAdminError(f'Auth service account échouée (HTTP {resp.status_code}).')
    data = resp.json()
    _token_cache['value'] = data['access_token']
    _token_cache['exp'] = now + int(data.get('expires_in', 60))
    return str(_token_cache['value'])


def _get(path: str, **kwargs):
    base, realm = _base_realm()
    try:
        resp = requests.get(f'{base}/admin/realms/{realm}{path}',
                            headers={'Authorization': f'Bearer {_token()}'},
                            timeout=_TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise KeycloakAdminError(f'Appel Keycloak échoué (GET {path}) : {exc}') from exc
    if resp.status_code >= 400:
        raise KeycloakAdminError(f'Keycloak GET {path} → HTTP {resp.status_code}')
    return resp.json()


def find_user_by_email(email: str) -> dict | None:
    users = _get('/users', params={'email': email, 'exact': 'true'})
    # `exact` filtre déjà ; on revérifie la casse côté app (emails normalisés).
    for u in users:
        if (u.get('email') or '').lower() == email.lower():
            return u
    return None
