"""Client de l'API MediaMTX (contrat I4) — transport seul, aucune décision.

API      (MEDIA_API_URL, 9997)      : état d'un chemin, sessions WebRTC/HLS, kick.
Playback (MEDIA_PLAYBACK_URL, 9996) : liste et extraction des enregistrements.
Les deux en Basic MEDIA_INTERNAL_USER:PASSWORD — le hook (I2 §3) n'accepte les
actions api/playback qu'avec ces identifiants.
"""
from __future__ import annotations

import requests
from django.conf import settings

_TIMEOUT = 5


class MediaError(RuntimeError):
    """MediaMTX injoignable ou réponse inattendue."""


def _auth() -> tuple[str, str]:
    return settings.MEDIA_INTERNAL_USER, settings.MEDIA_INTERNAL_PASSWORD


def _get(base: str, path: str, **kwargs) -> requests.Response:
    url = f'{base.rstrip("/")}{path}'
    try:
        resp = requests.get(url, auth=_auth(), timeout=kwargs.pop('timeout', _TIMEOUT), **kwargs)
    except requests.RequestException as exc:
        raise MediaError(f'MediaMTX injoignable ({path}) : {exc}') from exc
    return resp


def path_ready(path: str) -> bool:
    """`.ready` du chemin ; False si inconnu, non publié ou MediaMTX injoignable."""
    try:
        resp = _get(settings.MEDIA_API_URL, f'/v3/paths/get/{path}', timeout=2)
    except MediaError:
        return False
    if resp.status_code != 200:
        return False
    try:
        return bool(resp.json().get('ready'))
    except ValueError:
        return False


# Protocoles dont MediaMTX expose une liste + un kick de sessions (I4 v2).
# L'`id` reçu par le hook == `id` de la liste du protocole correspondant.
SESSION_ENDPOINTS = {'webrtc': 'webrtcsessions', 'hls': 'hlssessions'}


def list_sessions(protocol: str) -> list[dict]:
    """[{id, path, …}] de `/v3/<webrtc|hls>sessions/list`, toutes pages confondues."""
    endpoint = SESSION_ENDPOINTS[protocol]
    items: list[dict] = []
    page = 0
    while True:
        resp = _get(settings.MEDIA_API_URL, f'/v3/{endpoint}/list',
                    params={'page': page, 'itemsPerPage': 200})
        if resp.status_code != 200:
            raise MediaError(f'{endpoint}/list → HTTP {resp.status_code}')
        data = resp.json()
        items.extend(data.get('items') or [])
        page += 1
        if page >= int(data.get('pageCount') or 1):
            return items


def kick_session(protocol: str, session_id: str) -> None:
    endpoint = SESSION_ENDPOINTS[protocol]
    url = f'{settings.MEDIA_API_URL.rstrip("/")}/v3/{endpoint}/kick/{session_id}'
    try:
        resp = requests.post(url, auth=_auth(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise MediaError(f'kick {endpoint}/{session_id} : {exc}') from exc
    if resp.status_code not in (200, 404):
        # 404 : session déjà terminée entre la liste et le kick — but atteint.
        raise MediaError(f'kick {endpoint}/{session_id} → HTTP {resp.status_code}')


def playback_list(path: str) -> list[dict]:
    """[{start: iso, duration: float s}] ; [] si aucun enregistrement."""
    resp = _get(settings.MEDIA_PLAYBACK_URL, '/list', params={'path': path})
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise MediaError(f'playback /list → HTTP {resp.status_code}')
    return resp.json() or []


def playback_get(path: str, start_iso: str, duration_s: float) -> requests.Response:
    """Réponse **en flux** (stream=True) du fMP4 ; l'appelant la ferme."""
    url = f'{settings.MEDIA_PLAYBACK_URL.rstrip("/")}/get'
    try:
        resp = requests.get(
            url, auth=_auth(), stream=True, timeout=(_TIMEOUT, 60),
            params={'path': path, 'start': start_iso, 'duration': duration_s, 'format': 'fmp4'},
        )
    except requests.RequestException as exc:
        raise MediaError(f'playback /get : {exc}') from exc
    if resp.status_code != 200:
        resp.close()
        raise MediaError(f'playback /get → HTTP {resp.status_code}')
    return resp
