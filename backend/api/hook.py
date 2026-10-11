"""Hook de lecture de l'app (lab-media/API.md §2).

lab-media relaie ici CHAQUE demande de lecture d'un chemin `webcam/*` : le JSON
de MediaMTX {ip, user, password, token, action, path, protocol, id, query,
userAgent}, tel quel. 200 = autorisé, toute autre réponse = refusé ; le corps
est toujours vide.

Seule l'action `read` existe encore de ce côté : la publication, l'API, la
relecture et les métriques de MediaMTX sont tranchées par lab-media-auth et ne
parviennent plus à l'app. Toute autre action est donc REFUSÉE ici, y compris
avec les anciens identifiants de publication ou internes — cet endpoint ne doit
plus jamais pouvoir autoriser autre chose qu'une lecture.

L'URL est routée publiquement par Caddy (/peripheriques-api/api/media-auth/) et
joignable par toute app de dev-net : sans `?key=` correcte, rien ne doit
distinguer cet endpoint d'un refus banal — même code, même corps vide, quelle
que soit la méthode ou le contenu.
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


def _eq(given, expected: str) -> bool:
    """Comparaison en temps constant ; un attendu vide ne valide jamais rien."""
    if not expected or not isinstance(given, str):
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


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

    # 2. Lecture seulement. Toute autre action (publish, api, playback, metrics,
    #    pprof, inconnue) est refusée : elle n'a plus rien à faire ici.
    if body.get('action') != 'read':
        return _deny()

    # 3. JWT validé exactement comme l'API REST, puis droit sur le chemin.
    return _read(body, parse_path(body.get('path') or ''))


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
    """Garde la session pour pouvoir l'expulser à la révocation. Best-effort :
    un échec d'écriture ne doit pas refuser une lecture légitime."""
    try:
        MediaSession.objects.update_or_create(
            id=session_id[:100],
            defaults={'email': user.email, 'path': path, 'protocol': str(protocol)[:20]},
        )
        MediaSession.objects.filter(last_seen__lt=timezone.now() - SESSION_RETENTION).delete()
    except Exception:  # noqa: BLE001
        log.exception('Enregistrement de la session %s impossible', session_id)
