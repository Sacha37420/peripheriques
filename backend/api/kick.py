"""Coupure des lectures en direct après révocation d'un droit.

Synchrone dans la requête PATCH/DELETE (pas de Celery, contrat §7) et
best-effort : un lab-media injoignable est loggé, la révocation en base est déjà
acquise et n'est jamais annulée. Tant que lab-media est injoignable, aucune
NOUVELLE lecture n'est possible ; un lecteur déjà ouvert, lui, ne peut pas être
coupé avant son retour (lab-media/README.md, « Cycle de vie »).

L'expulsion passe par l'API de contrôle de lab-media (lab-media/API.md §1.2),
selon le `protocol` enregistré par le hook : `webrtc` ou `hls`. HLS DOIT être
coupé explicitement : le hook n'est appelé qu'à l'ouverture d'une session HLS,
la suite est servie sur secret de session. Tout autre protocole n'est pas
expulsé par l'app.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from . import media_client
from .access import kind_allowed, rights_for
from .models import MediaSession

log = logging.getLogger('api.kick')

# Une session autorisée par le hook peut ne pas encore figurer dans la liste de
# MediaMTX (négociation en cours) : on ne la purge pas trop tôt.
_GRACE = timedelta(minutes=5)


def kick_revoked(email: str, device_slug: str) -> int:
    """Coupe les sessions WebRTC/HLS de `email` sur `device_slug` que ses droits
    courants n'autorisent plus. Renvoie le nombre de sessions coupées."""
    rows = list(MediaSession.objects.filter(
        email=email.lower(), path__startswith=f'{device_slug}/',
        protocol__in=media_client.SESSION_PROTOCOLS,
    ))
    if not rows:
        return 0

    rights = rights_for(email, device_slug)
    kicked = 0
    for protocol in media_client.SESSION_PROTOCOLS:
        mine = [r for r in rows if r.protocol == protocol]
        if not mine:
            continue
        try:
            live = {s.get('id') for s in media_client.list_sessions(protocol)}
        except Exception as exc:  # noqa: BLE001
            log.warning('Kick %s impossible pour %s/%s : %s', protocol, email, device_slug, exc)
            continue
        for row in mine:
            if row.id not in live:
                if row.created_at < timezone.now() - _GRACE:
                    row.delete()
                continue
            if kind_allowed(rights, row.path.split('/', 1)[1]):
                continue
            try:
                media_client.kick_session(protocol, row.id)
            except Exception as exc:  # noqa: BLE001
                log.warning('Kick %s de la session %s (%s) échoué : %s',
                            protocol, row.id, row.path, exc)
                continue
            row.delete()
            kicked += 1
            log.info('Session %s %s (%s, %s) coupée après révocation',
                     protocol, row.id, email, row.path)
    return kicked
