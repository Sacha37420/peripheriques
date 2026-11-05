"""Pont « caméras d'un modèle » (contrat webcams-modeles v1, I1 et I2).

google-agenda (cadriciel dev2, autre realm) demande, par une clé serveur, les webcams sur
lesquelles un modèle a la vidéo, et reçoit un ticket de lecture court que le navigateur du
watcher présente ensuite en `Bearer` au WHEP de lab-media. peripheriques reste seul juge :
le hook (`api.hook`) revérifie à chaque ouverture le droit **du modèle** sur le chemin.

    POST /api/bridge/model-cameras/
    Authorization: Bridge <PERIPHERIQUES_BRIDGE_KEY>
    {"model_email": "...", "viewer_email": "..."}

Règles tenues ici :
  * la clé est contrôlée en tout premier, en temps constant, avant la méthode et le corps ;
    clé absente, fausse ou pont désactivé (clé vide) ⇒ le même `404 {"detail": "Not found."}` ;
  * vue Django simple, hors DRF : aucune classe d'authentification ne s'interpose, et un jeton
    Keycloak (`Bearer`) n'est simplement pas une clé de pont ⇒ 404 ;
  * la coupure (`Cutoff`, I0) n'est jamais consultée ;
  * on ne journalise que `viewer_email`, `model_email` et le nombre de périphériques — jamais
    la clé ni le ticket.

Contrat haut-parleur v1 (I4, I5) : la réponse gagne `speakers` (toujours présente, `[]` si
aucun) — les haut-parleurs où le modèle a le droit « Parler » ; le même ticket sert aussi à
PUBLIER (WHIP) sur leur chemin `talk`, le hook revérifiant le droit « Parler » du modèle.

Ticket (I2) : `pt1.` + `django.core.signing.dumps` (SECRET_KEY, sel propre) de
`{"m": model, "v": viewer, "exp": epoch}` — 120 s pour OUVRIR des sessions WebRTC.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import inventory
from .access import iso, kind_allowed, rights_for
from .models import Device

log = logging.getLogger('api.bridge')

TICKET_PREFIX = 'pt1.'
TICKET_SALT = 'peripheriques.bridge-ticket.v1'
TICKET_TTL_S = 120
_SCHEME = 'Bridge '
_FIELDS = {'model_email', 'viewer_email'}


# ── Clé ──────────────────────────────────────────────────────────────────────

def key_ok(header) -> bool:
    """`Authorization: Bridge <clé>` correct. Temps constant (empreintes de même longueur) ;
    une clé configurée vide ne valide jamais rien."""
    expected = settings.PERIPHERIQUES_BRIDGE_KEY or ''
    given = header[len(_SCHEME):] if isinstance(header, str) and header.startswith(_SCHEME) else ''
    a = hashlib.sha256(given.encode('utf-8', 'surrogatepass')).digest()
    b = hashlib.sha256(expected.encode('utf-8', 'surrogatepass')).digest()
    return hmac.compare_digest(a, b) and bool(expected) and bool(given)


def _not_found() -> JsonResponse:
    return JsonResponse({'detail': 'Not found.'}, status=404)


def _bad(detail: str) -> JsonResponse:
    return JsonResponse({'detail': detail}, status=400)


# ── Ticket ───────────────────────────────────────────────────────────────────

def issue_ticket(model_email: str, viewer_email: str) -> tuple[str, datetime]:
    exp = int(time.time()) + TICKET_TTL_S
    raw = signing.dumps({'m': model_email, 'v': viewer_email, 'exp': exp}, salt=TICKET_SALT)
    return TICKET_PREFIX + raw, datetime.fromtimestamp(exp, tz=timezone.utc)


def is_ticket(token) -> bool:
    return isinstance(token, str) and token.startswith(TICKET_PREFIX)


def read_ticket(token: str) -> tuple[str, str] | None:
    """(model_email, viewer_email) d'un ticket valide et non expiré ; None sinon."""
    if not is_ticket(token):
        return None
    try:
        # max_age : seconde barrière, indépendante du champ `exp` signé.
        payload = signing.loads(token[len(TICKET_PREFIX):], salt=TICKET_SALT,
                                max_age=TICKET_TTL_S + 1)
    except signing.BadSignature:          # SignatureExpired en hérite
        return None
    if not isinstance(payload, dict):
        return None
    model, viewer, exp = payload.get('m'), payload.get('v'), payload.get('exp')
    if not (isinstance(model, str) and model and isinstance(viewer, str) and viewer
            and isinstance(exp, int) and not isinstance(exp, bool)):
        return None
    if time.time() > exp:
        return None
    return model, viewer


# ── Vue I1 ───────────────────────────────────────────────────────────────────

def _email(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not value or len(value) > 254:
        return None
    try:
        validate_email(value)
    except ValidationError:
        return None
    return value


def model_devices(model_email: str) -> list[dict]:
    """Périphériques où le modèle a au moins la vidéo, triés par nom d'affichage (I1)."""
    out = []
    for device in sorted(Device.objects.filter(kind='webcam'),
                         key=lambda d: (d.label.casefold(), d.slug)):
        rights = rights_for(model_email, device)
        if not device.path_video or not kind_allowed(rights, 'video'):
            continue
        av = device.path_av if (device.has_audio and device.path_av
                                and kind_allowed(rights, 'av')) else None
        out.append({
            'id': device.slug,
            'label': device.label,
            'connected': device.connected,
            'state': device.state,
            'paths': {'video': device.path_video, 'av': av},
        })
    return out


def model_speakers(model_email: str) -> list[dict]:
    """Haut-parleurs où le modèle a le droit « Parler », triés par nom d'affichage (I4)."""
    out = []
    for device in sorted(Device.objects.filter(kind='speaker').exclude(path_talk=''),
                         key=lambda d: (d.label.casefold(), d.slug)):
        if not kind_allowed(rights_for(model_email, device), 'talk'):
            continue
        out.append({
            'id': device.slug,
            'label': device.label,
            'connected': device.connected,
            'state': device.state,
            'paths': {'talk': device.path_talk},
        })
    return out


@csrf_exempt
def model_cameras(request):
    # 1. Clé — avant toute autre chose, méthode comprise.
    if not key_ok(request.META.get('HTTP_AUTHORIZATION')):
        return _not_found()
    if request.method != 'POST':
        return JsonResponse({'detail': 'Méthode non autorisée.'}, status=405,
                            headers={'Allow': 'POST'})
    # 2. Corps : exactement {model_email, viewer_email}, deux emails valides.
    try:
        body = json.loads(request.body or b'')
    except (ValueError, UnicodeDecodeError):
        return _bad('Corps JSON attendu.')
    if not isinstance(body, dict) or set(body) != _FIELDS:
        return _bad('Champs attendus : model_email, viewer_email.')
    model, viewer = _email(body['model_email']), _email(body['viewer_email'])
    if model is None or viewer is None:
        return _bad('Email invalide.')
    # 3. Inventaire relu au plus une fois par DEVICE_SYNC_TTL_S ; injoignable ⇒ dernier état connu.
    inventory.sync()
    devices = model_devices(model)
    speakers = model_speakers(model)
    ticket, expires_at = issue_ticket(model, viewer) if (devices or speakers) else (None, None)
    log.info('Pont : %s demande les caméras de %s — %d périphérique(s), %d haut-parleur(s)',
             viewer, model, len(devices), len(speakers))
    return JsonResponse({'model_email': model, 'devices': devices, 'speakers': speakers,
                         'ticket': ticket, 'expires_at': iso(expires_at)})
