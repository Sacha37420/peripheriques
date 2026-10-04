"""Authentification Keycloak — Verrou 2 (serrure de l'API).

La validation d'un JWT vit dans **une seule fonction**, `validate_token`, utilisée
par deux portes d'entrée :

  * l'API REST (`KeycloakJWTAuthentication`, header `Authorization: Bearer`) ;
  * le hook d'autorisation MediaMTX (`api.hook`, champ `token` du corps JSON),
    qui protège la lecture en direct WHEP/HLS (contrat I2 §4).

Les deux appliquent donc exactement les mêmes règles — signature, `exp`,
`azp == KEYCLOAK_CLIENT_ID` (jamais `admin-cli`, public avec password grant
sur le realm), groupe requis, claim `email`. Ne jamais dupliquer ces contrôles
ailleurs : une divergence entre les deux portes ouvrirait le direct à ce que
l'API refuse.

Écart assumé par rapport au template, repris d'`oauth-hub` : l'accès à l'app est
« au moins un groupe du lab » (`--require-group` reçoit la liste complète), et un
`KEYCLOAK_REQUIRED_GROUPS` **vide** est refusé au lieu de désactiver le filtre.
Le template laisse alors passer tout le realm, y compris un compte
auto-inscrit sans groupe — inacceptable pour une app qui ouvre une caméra.
"""
import jwt
from jwt import PyJWKClient, InvalidTokenError, PyJWKClientError
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.conf import settings


def _csv(value: str) -> set[str]:
    return {g.strip() for g in (value or '').split(',') if g.strip()}


def admin_groups() -> set[str]:
    return _csv(settings.PERIPHERIQUES_ADMIN_GROUPS)


class KeycloakUser:
    """Identité construite depuis les claims du JWT Keycloak."""

    is_authenticated = True
    is_active = True

    def __init__(self, claims: dict) -> None:
        # Les droits sont stockés en minuscules (I3) : on compare toujours
        # l'email en minuscules, quelle que soit la casse dans le realm.
        self.email: str = (claims.get('email') or '').strip().lower()
        self.username: str = claims.get('preferred_username', self.email)
        self.groups: set[str] = set(claims.get('groups') or [])
        self.claims = claims
        self.pk = self.email

    @property
    def is_admin(self) -> bool:
        return bool(admin_groups() & self.groups)

    def __str__(self) -> str:
        return self.username


_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            f'{settings.KEYCLOAK_ISSUER_URI}/protocol/openid-connect/certs',
            cache_keys=True,
        )
    return _jwks_client


def validate_token(token: str) -> KeycloakUser:
    """Valide un JWT Keycloak brut (sans préfixe « Bearer »).

    Lève `AuthenticationFailed` au premier contrôle qui échoue ; renvoie
    l'identité sinon. Seule implémentation du Verrou 2 de cette app.
    """
    if not token:
        raise AuthenticationFailed('Token absent.')
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=['RS256'],
            options={
                'verify_exp': True,
                # Keycloak ne met pas le clientId dans `aud` sans mapper dédié :
                # c'est `azp` qui porte le contrôle, juste en dessous.
                'verify_aud': False,
            },
        )
    except (InvalidTokenError, PyJWKClientError) as exc:
        raise AuthenticationFailed(f'Token invalide : {exc}') from exc

    # Le token doit avoir été émis POUR cette application. Sans ce contrôle,
    # tout compte du realm obtient un token via `admin-cli` (client public,
    # password grant activé par défaut) et passe.
    if claims.get('azp') != settings.KEYCLOAK_CLIENT_ID:
        raise AuthenticationFailed(
            "Ce token a été émis pour un autre client que "
            f"'{settings.KEYCLOAK_CLIENT_ID}'."
        )

    required = _csv(settings.KEYCLOAK_REQUIRED_GROUPS)
    if not required:
        raise AuthenticationFailed(
            "KEYCLOAK_REQUIRED_GROUPS est vide dans peripheriques/.env : l'app "
            "s'ouvrirait à tout compte du realm. Relancez create-app-client.sh "
            "avec --require-group."
        )
    if not required & set(claims.get('groups') or []):
        raise AuthenticationFailed(
            "Accès réservé aux comptes rattachés à au moins un groupe du lab."
        )

    if not claims.get('email'):
        raise AuthenticationFailed(
            "Le token ne contient pas de claim 'email'. "
            "Vérifiez que le mapper 'email' est activé dans Keycloak."
        )

    return KeycloakUser(claims)


class KeycloakJWTAuthentication(BaseAuthentication):
    """Porte REST : `Authorization: Bearer <jwt>` → `validate_token`."""

    def authenticate(self, request):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return None
        token = auth_header[7:]
        return validate_token(token), token

