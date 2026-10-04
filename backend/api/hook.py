"""Hook d'autorisation MediaMTX (contrat I2) — `authMethod: http`.

MediaMTX POSTe ici, pour CHAQUE publication / lecture / appel API / playback,
le JSON {user, password, token, ip, action, path, protocol, id, query}.
200 = autorisé, 401 = refusé, corps toujours vide.

L'URL est routée publiquement par Caddy (/peripheriques-api/api/media-auth/) :
sans `?key=` correcte, rien ne doit distinguer cet endpoint d'un refus banal —
même code, même corps vide, quelle que soit la méthode ou le contenu.
"""
from __future__ import annotations

import hmac
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import AuthenticationFailed

from .access import kind_allowed, parse_path, rights_for
from .authentication import validate_token
from .models import Device, MediaSession

log = logging.getLogger('api.hook')

# Filet de sécurité de purge de la table des sessions (cf. MediaSession).
SESSION_RETENTION = timedelta(days=30)

INTERNAL_ACTIONS = {'playback', 'api', 'metrics', 'pprof'}


def _eq(given, expected: str) -> bool:
    """Comparaison en temps constant ; un attendu vide ne valide jamais rien."""
    if not expected or not isinstance(given, str):
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def _creds(body: dict, user: str, password: str) -> bool:
    # Pas de court-circuit : les deux comparaisons sont toujours faites.
    ok_user = _eq(body.get('user'), user)
    ok_pass = _eq(body.get('password'), password)
    return ok_user and ok_pass


def _allow() -> HttpResponse:
    return HttpResponse(status=200)


def _deny() -> HttpResponse:
    return HttpResponse(status=401)


@csrf_exempt
def media_auth(request):
    # 1. Clé partagée — avant toute autre chose, y compris la méthode.
    if not _eq(request.GET.get('key', ''), settings.MEDIA_HOOK_KEY):
        return _deny()
    if request.method != 'POST':
        return _deny()
    try:
        body = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return _deny()
    if not isinstance(body, dict):
        return _deny()

    action = body.get('action')
    parsed = parse_path(body.get('path') or '')

    # 2. Publication : ffmpeg seul, sur un chemin I1 d'un périphérique connu.
    if action == 'publish':
        if not _creds(body, settings.MEDIA_PUBLISH_USER, settings.MEDIA_PUBLISH_PASSWORD):
            return _deny()
        if parsed is None or not Device.objects.filter(slug=parsed[0]).exists():
            return _deny()
        return _allow()

    # 3. API / playback / métriques : le backend seul, jamais un navigateur.
    if action in INTERNAL_ACTIONS:
        if _creds(body, settings.MEDIA_INTERNAL_USER, settings.MEDIA_INTERNAL_PASSWORD):
            return _allow()
        return _deny()

    # 4. Lecture en direct : JWT validé exactement comme l'API REST.
    if action == 'read':
        return _read(body, parsed)

    # 5. Tout le reste.
    return _deny()


def _read(body: dict, parsed) -> HttpResponse:
    if parsed is None:
        return _deny()
    slug, kind = parsed
    token = body.get('token') or ''
    if not isinstance(token, str):
        return _deny()
    if token.startswith('Bearer '):
        token = token[7:]
    try:
        user = validate_token(token)
    except AuthenticationFailed:
        return _deny()

    device = Device.objects.filter(slug=slug).first()
    if device is None:
        return _deny()
    if not kind_allowed(rights_for(user.email, device), kind):
        return _deny()

    session_id = body.get('id')
    if isinstance(session_id, str) and session_id:
        _remember(session_id, user, f'{slug}/{kind}', body.get('protocol') or '')
    return _allow()


def _remember(session_id: str, user, path: str, protocol: str) -> None:
    """Garde la session pour pouvoir la couper à la révocation (I4). Best-effort :
    un échec d'écriture ne doit pas refuser une lecture légitime."""
    try:
        MediaSession.objects.update_or_create(
            id=session_id[:100],
            defaults={'email': user.email, 'path': path, 'protocol': str(protocol)[:20]},
        )
        MediaSession.objects.filter(last_seen__lt=timezone.now() - SESSION_RETENTION).delete()
    except Exception:  # noqa: BLE001
        log.exception('Enregistrement de la session %s impossible', session_id)
