"""Synchronisation de l'inventaire de lab-media vers `Device` (contrat webcams v1, I0/I1).

Quand : à la lecture des listes de périphériques (`/api/devices/`, `/api/grants/devices/`,
`/api/admin/devices/`), au plus une fois par `DEVICE_SYNC_TTL_S` secondes et par processus.
Jamais dans le hook de lecture : il ne décide que sur la base, sans appel sortant.

Règles :
  * un périphérique rendu par I1 est créé à la première lecture, nom d'affichage = `model`
    (texte brut, jamais interprété) ; ce nom n'est ensuite plus jamais réécrit ici ;
  * les noms sont uniques (casse et espaces de bord ignorés, contrainte en base) : si `model`
    est déjà pris, le nouveau reçoit « model 2 », « model 3 »… dans l'ordre `first_seen` puis
    identifiant (le premier arrivé garde le nom nu) ;
  * tout le reste (modèle, port, micro, état, chemins…) suit I1 à chaque synchronisation ;
  * lab-media injoignable ou réponse illisible : rien n'est modifié, les périphériques déjà
    connus restent utilisables, l'état « inventaire injoignable » est exposé aux admins ;
  * une entrée mal formée est ignorée (journalisée) sans empêcher les autres ;
  * un chemin déjà porté par un autre périphérique fait ignorer l'entrée : un périphérique ne
    peut jamais hériter des droits d'un autre en reprenant ses chemins ;
  * un périphérique connu ici mais absent d'une liste lue avec succès n'est pas supprimé
    (ses droits non plus) : il est marqué `listed=False` et un admin peut l'oublier.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import media_client
from .access import parse_iso
from .models import Device

log = logging.getLogger('api.inventory')

ID_RE = re.compile(r'^usb-[0-9a-f]{4}-[0-9a-f]{4}-[sp][a-z0-9-]{1,40}$')
LEGACY_ID = 'webcam-ancienne'
LEGACY_LABEL = 'Ancienne webcam'
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
# Segments sans « . » ni « .. » (aucun segment ne commence par un point).
_PATH_RE = re.compile(r'^[A-Za-z0-9_~-][A-Za-z0-9._~-]*(/[A-Za-z0-9_~-][A-Za-z0-9._~-]*)*$')

_lock = threading.Lock()
_last_attempt: float | None = None
_status: dict = {'reachable': None, 'checked_at': None}


def status() -> dict:
    """{'reachable': bool|None, 'checked_at': datetime|None} — dernière tentative."""
    return dict(_status)


def invalidate() -> None:
    """Force la prochaine lecture à resynchroniser (après une action d'admin)."""
    global _last_attempt
    _last_attempt = None


def sync(force: bool = False) -> bool:
    """Synchronise si le cache est périmé. Renvoie l'état joignable de la dernière tentative.

    Un seul appel à la fois par processus ; une requête concurrente n'attend pas, elle lit
    la base telle qu'elle est."""
    global _last_attempt
    ttl = float(getattr(settings, 'DEVICE_SYNC_TTL_S', 10))
    now = time.monotonic()
    if not force and _last_attempt is not None and now - _last_attempt < ttl:
        return bool(_status['reachable'])
    if not _lock.acquire(blocking=False):
        return bool(_status['reachable'])
    try:
        _last_attempt = now
        try:
            items = media_client.list_devices()
        except media_client.MediaError as exc:
            log.warning('Inventaire de lab-media injoignable, périphériques connus gardés : %s', exc)
            _set_status(False)
            return False
        apply(items)
        _set_status(True)
        return True
    finally:
        _lock.release()


def _set_status(reachable: bool) -> None:
    _status.update(reachable=reachable, checked_at=timezone.now())


def clean_text(value, limit: int) -> str:
    """Texte brut d'une source externe : chaîne, sans caractère de contrôle, bornée."""
    if not isinstance(value, str):
        return ''
    return _CONTROL.sub('', value).strip()[:limit]


def _valid_path(value) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 120 and bool(_PATH_RE.match(value))


def _parse(item) -> dict | None:
    """Champs d'un objet I1 validés ; None si l'entrée est inutilisable."""
    if not isinstance(item, dict):
        return None
    dev_id = item.get('id')
    if not isinstance(dev_id, str) or not (dev_id == LEGACY_ID or ID_RE.match(dev_id)):
        return None
    paths = item.get('paths')
    if not isinstance(paths, dict):
        return None
    av, video, audio = paths.get('av'), paths.get('video'), paths.get('audio')
    if not (_valid_path(av) and _valid_path(video)):
        return None
    if audio is not None and not _valid_path(audio):
        return None
    if len({av, video, audio} - {None}) != (3 if audio else 2):
        return None                          # deux kinds sur un même chemin : incohérent

    def when(key) -> datetime | None:
        try:
            return parse_iso(item.get(key))
        except ValueError:
            return None

    state = item.get('state')
    return {
        'slug': dev_id,
        'model': clean_text(item.get('model'), 100),
        'vendor_id': clean_text(item.get('vendor_id'), 4),
        'product_id': clean_text(item.get('product_id'), 4),
        'serial': clean_text(item.get('serial'), 64),
        'usb_port': clean_text(item.get('usb_port'), 64),
        'has_audio': item.get('has_audio') is True and audio is not None,
        'connected': item.get('connected') is True,
        'state': state if state in Device.STATES else '',
        'error_detail': clean_text(item.get('error_detail'), 300) if state == 'error' else '',
        'record': item.get('record') is True,
        'first_seen': when('first_seen'),
        'last_seen': when('last_seen'),
        'path_av': av,
        'path_video': video,
        'path_audio': audio or '',
        'listed': True,
    }


def numbered(base: str, n: int, limit: int = 100) -> str:
    """« base » pour n = 1, « base 2 », « base 3 »… ; la base est raccourcie si besoin."""
    if n <= 1:
        return base[:limit]
    suffix = f' {n}'
    return base[:limit - len(suffix)].rstrip() + suffix


MAX_NUMBER = 1000


def _create(slug: str, base: str, fields: dict) -> bool:
    """Crée le périphérique sous le premier nom libre : « base », puis « base 2 », « base 3 »…

    Le contrôle préalable évite presque toutes les collisions ; la contrainte d'unicité de la
    base tranche les courses entre processus : une IntegrityError fait essayer le numéro
    suivant (dans un point de reprise), jamais une erreur 500. Renvoie False si un autre
    processus a créé le même périphérique entre-temps."""
    for n in range(1, MAX_NUMBER + 1):
        label = numbered(base, n)
        if Device.label_taken(label):
            continue
        try:
            with transaction.atomic():
                Device.objects.create(slug=slug, label=label, **fields)
            return True
        except IntegrityError:
            if Device.objects.filter(slug=slug).exists():
                return False
    raise RuntimeError(f'aucun nom libre pour {slug}')


def apply(items) -> None:
    """Applique une liste I1 lue avec succès (voir les règles en tête de module).

    Ordre de traitement déterministe — `first_seen` puis identifiant — pour que le premier
    arrivé garde le nom nu et que deux synchronisations concurrentes numérotent pareil."""
    parsed = []
    for item in items if isinstance(items, list) else []:
        fields = _parse(item)
        if fields is None:
            log.warning('Inventaire : entrée ignorée (forme inattendue)')
            continue
        parsed.append(fields)
    early = datetime.min.replace(tzinfo=dt_timezone.utc)   # jamais daté = le plus ancien (héritée)
    parsed.sort(key=lambda f: (f['first_seen'] or early, f['slug']))
    seen: set[str] = set()
    with transaction.atomic():
        for fields in parsed:
            slug = fields.pop('slug')
            if slug in seen:
                continue
            own_paths = {fields['path_av'], fields['path_video'], fields['path_audio']} - {''}
            clash = Device.objects.exclude(slug=slug).filter(
                Q(path_av__in=own_paths) | Q(path_video__in=own_paths)
                | Q(path_audio__in=own_paths)).exists()
            if clash:
                log.warning('Inventaire : %s ignoré, un de ses chemins appartient déjà à un autre '
                            'périphérique', slug)
                continue
            seen.add(slug)
            device = Device.objects.filter(slug=slug).first()
            if device is None:
                base = LEGACY_LABEL if slug == LEGACY_ID else (fields['model'] or slug)
                if _create(slug, base, fields):
                    log.info('Inventaire : nouveau périphérique %s', slug)
                    continue
                device = Device.objects.get(slug=slug)
            changed = [k for k, v in fields.items() if getattr(device, k) != v]
            if changed:
                for k in changed:
                    setattr(device, k, fields[k])
                device.save(update_fields=changed)
        Device.objects.exclude(slug__in=seen).filter(listed=True).update(listed=False)


def device_fields(item) -> dict | None:
    """Champs d'un objet I1 isolé (réponse de PATCH) ; None s'il est inutilisable."""
    fields = _parse(item)
    if fields is not None:
        fields.pop('slug')
    return fields
