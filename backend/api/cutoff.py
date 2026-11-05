"""Coupure globale du direct et des enregistrements (contrat webcams-modeles v1, I0).

    GET  /api/admin/cutoff/                 état
    POST /api/admin/cutoff/  {"active": b}  activer / désactiver — rejouable

Réservé aux admins des droits (`PERIPHERIQUES_ADMIN_GROUPS`).

Activer :
  1. la coupure est inscrite en base et validée AVANT tout appel à lab-media : dès cet instant
     le hook refuse toute lecture par jeton Keycloak (fail closed, même lab-media injoignable) ;
  2. toutes les lectures en cours sont expulsées, WebRTC et HLS, sauf celles du pont (I2) ;
  3. chaque webcam dont l'enregistrement est actif passe à `record=false` dans lab-media et
     entre dans `paused` — la liste exacte de ce qu'il faudra rétablir.
Désactiver : exactement les webcams de `paused` repassent à `record=true` ; une webcam oubliée
entre-temps (ici ou dans lab-media) est ignorée ; puis la coupure est levée.

Tout échec de lab-media est gardé en base (`failures`, `media_errors`), rendu à l'admin, et
rejoué en renvoyant la même demande. La relecture des enregistrements n'est pas concernée.
"""
from __future__ import annotations

import logging

from django.db import transaction
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory, media_client
from .access import iso, now_s
from .kick import kick_direct
from .models import Cutoff, Device
from .permissions import IsPeripheriquesAdmin

log = logging.getLogger('api.cutoff')


def state(cutoff: Cutoff | None = None) -> dict:
    cutoff = cutoff or Cutoff.current()
    labels = dict(Device.objects.values_list('slug', 'label'))
    return {
        'active': cutoff.active,
        'since': iso(cutoff.since),
        'by': cutoff.by or None,
        'paused': [{'id': s, 'label': labels.get(s, s)} for s in cutoff.paused],
        'failures': [{'id': f['id'], 'label': labels.get(f['id'], f['id']), 'action': f['action']}
                     for f in cutoff.failures],
        'media_errors': list(cutoff.media_errors),
    }


def _set_record(slug: str, record: bool) -> str:
    """'ok' | 'gone' (inconnu de lab-media, ou ancienne webcam) | 'failed'."""
    try:
        item = media_client.set_device_record(slug, record)
    except media_client.DeviceRefused as exc:
        if exc.status in (404, 409):
            return 'gone'
        log.warning('Coupure : enregistrement de %s → %s refusé : %s', slug, record, exc)
        return 'failed'
    except media_client.MediaError as exc:
        log.warning('Coupure : enregistrement de %s → %s impossible : %s', slug, record, exc)
        return 'failed'
    Device.objects.filter(slug=slug).update(record=item.get('record') is True)
    return 'ok'


def activate(by: str) -> Cutoff:
    # 1. Acquis en base d'abord (le hook relit cette ligne à chaque lecture).
    with transaction.atomic():
        cutoff = Cutoff.locked()
        if not cutoff.active:
            cutoff.active, cutoff.since, cutoff.by = True, now_s(), by
            cutoff.save()
            log.info('Coupure activée par %s', by)
    media_errors: list[str] = []
    # 2. Expulsion des lectures par jeton Keycloak.
    sessions_errors: list[str] = []
    kick_direct(sessions_errors)
    if sessions_errors:
        media_errors.append('sessions')
    # 3. Enregistrements. L'inventaire est relu (forcé) ; injoignable ⇒ dernier état connu.
    if not inventory.sync(force=True):
        media_errors.append('inventory')
    with transaction.atomic():
        cutoff = Cutoff.locked()
        paused = list(cutoff.paused)
        failures = [f for f in cutoff.failures if f['action'] != 'stop']
        for slug in Device.objects.filter(record=True).order_by('slug').values_list('slug', flat=True):
            result = _set_record(slug, False)
            if result == 'gone':
                continue
            if slug not in paused:
                paused.append(slug)
            if result == 'failed':
                failures.append({'id': slug, 'action': 'stop'})
        cutoff.paused, cutoff.failures, cutoff.media_errors = paused, failures, media_errors
        cutoff.save()
    inventory.invalidate()
    return cutoff


def deactivate(by: str) -> Cutoff:
    with transaction.atomic():
        cutoff = Cutoff.locked()
        remaining, failures = [], []
        for slug in cutoff.paused:
            if not Device.objects.filter(slug=slug).exists():
                continue                                  # oubliée entre-temps : ignorée
            result = _set_record(slug, True)
            if result == 'failed':
                remaining.append(slug)
                failures.append({'id': slug, 'action': 'restore'})
        was_active = cutoff.active
        cutoff.active, cutoff.since, cutoff.by = False, None, ''
        cutoff.paused, cutoff.failures, cutoff.media_errors = remaining, failures, []
        cutoff.save()
    if was_active:
        log.info('Coupure levée par %s', by)
    inventory.invalidate()
    return cutoff


def forget(slug: str) -> None:
    """Une webcam oubliée ou réglée à la main sort de ce que la coupure rétablira."""
    with transaction.atomic():
        cutoff = Cutoff.locked()
        paused = [s for s in cutoff.paused if s != slug]
        failures = [f for f in cutoff.failures if f['id'] != slug]
        if paused != cutoff.paused or failures != cutoff.failures:
            cutoff.paused, cutoff.failures = paused, failures
            cutoff.save()


class CutoffView(APIView):
    permission_classes = [IsPeripheriquesAdmin]

    def get(self, request):
        return Response(state())

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else None
        if not body or set(body) != {'active'} or not isinstance(body['active'], bool):
            return Response({'detail': 'Corps attendu : {"active": true | false}.'}, status=400)
        if body['active']:
            cutoff = activate(request.user.email)
        else:
            cutoff = deactivate(request.user.email)
        return Response(state(cutoff))
