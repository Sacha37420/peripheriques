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

from . import bridge
from .access import kind_allowed, rights_for
from .authentication import validate_token
from .models import Cutoff, Device, MediaSession

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

    # 3. Périphérique retrouvé par ses chemins connus (I2), puis JWT validé
    #    exactement comme l'API REST, puis droit sur ce chemin.
    return _read(body, body.get('path'))


def _read(body: dict, path) -> HttpResponse:
    if isinstance(path, str) and path.startswith('/'):
        path = path[1:]
    device, kind = Device.by_path(path)
    if device is None:
        return _deny()
    token = body.get('token') or ''
    if not isinstance(token, str):
        return _deny()
    if token.startswith('Bearer '):
        token = token[7:]

    # Ticket du pont (I2) — reconnu à son préfixe AVANT toute validation de JWT. La coupure
    # (I0) n'est pas consultée sur ce chemin.
    if bridge.is_ticket(token):
        return _read_ticket(body, token, device, kind, path)

    # Jeton Keycloak : refusé d'office tant que la coupure tient (relue en base à chaque appel).
    if Cutoff.is_active():
        return _deny()
    try:
        user = validate_token(token)
    except AuthenticationFailed:
        return _deny()

    if not kind_allowed(rights_for(user.email, device), kind):
        return _deny()

    session_id = body.get('id')
    if isinstance(session_id, str) and session_id:
        _remember(session_id, user.email, path, body.get('protocol') or '')
    return _allow()


def _read_ticket(body: dict, token: str, device: Device, kind: str, path: str) -> HttpResponse:
    """Lecture par ticket : signature et validité (120 s), WebRTC seulement, et droit du
    MODÈLE sur ce chemin au moment même du hook.

    La session est gardée au nom du modèle (sa révocation coupe ses watchers par
    `kick_revoked`), marquée « pont » (la coupure l'épargne). Contrairement à une lecture par
    JWT, une session qu'on ne peut pas garder est refusée : sans ligne, ni la révocation du
    modèle ni l'épargne de la coupure ne sauraient la retrouver."""
    claims = bridge.read_ticket(token)
    if claims is None:
        return _deny()
    model_email, viewer_email = claims
    if body.get('protocol') != 'webrtc':
        return _deny()
    if not kind_allowed(rights_for(model_email, device), kind):
        return _deny()
    session_id = body.get('id')
    if not (isinstance(session_id, str) and session_id):
        return _deny()
    if not _remember(session_id, model_email, path, 'webrtc', viewer_email=viewer_email):
        return _deny()
    log.info('Lecture par le pont : %s regarde %s (droits de %s)', viewer_email, path, model_email)
    return _allow()


def _remember(session_id: str, email: str, path: str, protocol: str,
              viewer_email: str | None = None) -> bool:
    """Garde la session pour pouvoir l'expulser à la révocation. Best-effort pour une
    lecture par JWT : un échec d'écriture ne doit pas refuser une lecture légitime."""
    try:
        MediaSession.objects.update_or_create(
            id=session_id[:100],
            defaults={'email': email, 'path': path, 'protocol': str(protocol)[:20],
                      'bridge': viewer_email is not None,
                      'viewer_email': viewer_email or ''},
        )
        MediaSession.objects.filter(last_seen__lt=timezone.now() - SESSION_RETENTION).delete()
    except Exception:  # noqa: BLE001
        log.exception('Enregistrement de la session %s impossible', session_id)
        return False
    return True
