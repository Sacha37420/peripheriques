"""Outils de test partagés : vrais JWT RS256 signés par une clé de test.

`validate_token` est exécuté tel quel — seule la récupération du JWKS est
remplacée (la clé publique de test au lieu de celle de Keycloak). Les contrôles
signature/exp/azp/groupes/email sont donc réellement exercés.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from api import authentication

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

ADMIN = 'admin@ssolab.local'
MEMBER = 'member@ssolab.local'
OTHER = 'other@ssolab.local'


class _FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=_KEY.public_key())


def patch_jwks():
    """Context manager / décorateur : JWKS = clé publique de test."""
    # `new=` : aucun argument mock injecté dans les méthodes de test décorées.
    return mock.patch.object(authentication, '_get_jwks_client', new=lambda: _FakeJWKS())


def make_token(email: str | None = MEMBER, groups=('famille',), azp='peripheriques',
               exp_delta: int = 300, foreign_key: bool = False) -> str:
    now = int(time.time())
    claims = {'azp': azp, 'groups': list(groups), 'iat': now, 'exp': now + exp_delta,
              'preferred_username': (email or 'x').split('@')[0]}
    if email is not None:
        claims['email'] = email
    return jwt.encode(claims, _OTHER_KEY if foreign_key else _KEY, algorithm='RS256')


def admin_token() -> str:
    return make_token(ADMIN, groups=('developers',))


def bearer(token: str) -> dict:
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}
