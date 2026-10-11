"""Client de l'API de contrôle de lab-media (lab-media/API.md §1) — transport seul, aucune décision.

Le backend ne parle plus jamais à MediaMTX. Tout passe par `lab-media-auth`
(MEDIA_API_URL, réseau dev-net), en Basic avec les identifiants de contrôle de
l'app (MEDIA_INTERNAL_USER / MEDIA_INTERNAL_PASSWORD), qui ne donnent accès
qu'aux chemins de l'app :

    GET  /v1/paths/<chemin>                       état d'un chemin
    GET  /v1/sessions?protocol=webrtc|hls         sessions en cours
    POST /v1/sessions/<protocol>/<id>/kick        expulsion d'un lecteur
    GET  /v1/recordings?path=<chemin>             plages enregistrées
    GET  /v1/recordings/get?path=&start=&duration=&format=fmp4    une plage, en flux

Brique injoignable, réponse inattendue ou identifiants refusés : `MediaError`
(ou `False` pour `path_ready`). Aucune de ces pannes n'ouvre quoi que ce soit —
la lecture en direct, elle, est refusée par la brique tant qu'elle ne peut pas
interroger notre hook.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import requests
from django.conf import settings

log = logging.getLogger('api.media')

_TIMEOUT = 5

# Protocoles dont une session de lecture peut être listée et expulsée par l'app.
# L'`id` reçu par le hook == `id` de la liste (API.md §1.2). HLS DOIT en faire
# partie : son hook n'est appelé qu'à l'ouverture de la session.
SESSION_PROTOCOLS = ('webrtc', 'hls')


class MediaError(RuntimeError):
    """lab-media injoignable ou réponse inattendue."""


class MediaAuthError(MediaError):
    """lab-media a refusé nos identifiants de contrôle (401) : erreur de
    configuration (MEDIA_INTERNAL_USER/PASSWORD ≠ lab-media/.env), pas une panne."""


def _auth() -> tuple[str, str]:
    return settings.MEDIA_INTERNAL_USER, settings.MEDIA_INTERNAL_PASSWORD


def _request(method: str, path: str, *, what: str, timeout=_TIMEOUT, **kwargs) -> requests.Response:
    url = f'{settings.MEDIA_API_URL.rstrip("/")}{path}'
    try:
        resp = requests.request(method, url, auth=_auth(), timeout=timeout, **kwargs)
    except requests.RequestException as exc:
        raise MediaError(f'lab-media injoignable ({what}) : {type(exc).__name__}') from exc
    if resp.status_code == 401:
        resp.close()
        log.error('lab-media refuse les identifiants de contrôle (%s) : vérifier '
                  'MEDIA_INTERNAL_USER/PASSWORD et lab-media/.env', what)
        raise MediaAuthError(f'identifiants de contrôle refusés ({what})')
    return resp


def path_ready(path: str) -> bool:
    """`.ready` du chemin ; False si inconnu, non publié, ou lab-media
    injoignable / identifiants refusés (le périphérique s'affiche « hors ligne »)."""
    try:
        resp = _request('GET', f'/v1/paths/{quote(path)}', what='état du chemin', timeout=2)
    except MediaError:
        return False
    if resp.status_code != 200:
        return False
    try:
        return resp.json().get('ready') is True
    except (ValueError, AttributeError):
        return False


def list_sessions(protocol: str) -> list[dict]:
    """[{id, path, state, …}] : sessions en cours de `protocol` sur NOS chemins."""
    if protocol not in SESSION_PROTOCOLS:
        raise KeyError(protocol)
    resp = _request('GET', '/v1/sessions', what=f'sessions {protocol}', params={'protocol': protocol})
    if resp.status_code != 200:
        raise MediaError(f'sessions {protocol} → HTTP {resp.status_code}')
    try:
        items = resp.json()['items']
    except (ValueError, KeyError, TypeError) as exc:
        raise MediaError(f'sessions {protocol} : réponse illisible') from exc
    if not isinstance(items, list):
        raise MediaError(f'sessions {protocol} : réponse illisible')
    return items


def kick_session(protocol: str, session_id: str) -> None:
    """Expulse un lecteur. 204 : expulsé ; 404 : session déjà terminée — but atteint."""
    if protocol not in SESSION_PROTOCOLS:
        raise KeyError(protocol)
    resp = _request('POST', f'/v1/sessions/{protocol}/{quote(session_id, safe="")}/kick',
                    what=f'expulsion {protocol}')
    if resp.status_code not in (204, 404):
        raise MediaError(f'expulsion {protocol}/{session_id} → HTTP {resp.status_code}')


def playback_list(path: str) -> list[dict]:
    """[{start: iso, duration: float s}] ; [] si aucun enregistrement."""
    resp = _request('GET', '/v1/recordings', what='liste des enregistrements', params={'path': path})
    if resp.status_code != 200:
        # 404 = « pas un chemin de l'app » côté lab-media : erreur de registre, pas une liste vide.
        raise MediaError(f'liste des enregistrements → HTTP {resp.status_code}')
    try:
        items = resp.json()
    except ValueError as exc:
        raise MediaError('liste des enregistrements : réponse illisible') from exc
    if not isinstance(items, list):
        raise MediaError('liste des enregistrements : réponse illisible')
    return items


def playback_get(path: str, start_iso: str, duration_s: float) -> requests.Response:
    """Réponse **en flux** (stream=True) du fMP4 ; l'appelant la ferme."""
    resp = _request(
        'GET', '/v1/recordings/get', what='lecture d\'une plage', stream=True, timeout=(_TIMEOUT, 60),
        params={'path': path, 'start': start_iso, 'duration': duration_s, 'format': 'fmp4'},
    )
    if resp.status_code != 200:
        resp.close()
        raise MediaError(f'lecture d\'une plage → HTTP {resp.status_code}')
    return resp
