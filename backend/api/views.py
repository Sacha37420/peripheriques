import json
import logging

from django.conf import settings
from django.http import HttpResponse

from django.db import transaction
from django.db.models import Count, F
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import inventory, keycloak_admin, media_client
from .access import CAPS, has_any, iso, now_s, rights_for
from .kick import kick_paths, kick_revoked
from .models import Device, Grant
from .permissions import IsPeripheriquesAdmin
from .serializers import (NO_ACCOUNT, GrantCreateSerializer, GrantSerializer,
                          GrantUpdateSerializer)

log = logging.getLogger('api.views')


class KeycloakUnavailable(APIException):
    status_code = 503
    default_detail = "Impossible de vérifier l'email auprès de Keycloak pour l'instant."


class MeView(APIView):
    """GET /api/me/ → {email, is_admin}"""

    def get(self, request):
        return Response({'email': request.user.email, 'is_admin': request.user.is_admin})


def devices_in_order():
    """Ordre de I1 : premier branchement d'abord ; l'inconnu (jamais vu) en dernier."""
    return Device.objects.order_by(F('first_seen').asc(nulls_last=True), 'slug')


def _online(device: Device) -> bool:
    return bool(device.path_av) and media_client.path_ready(device.path_av)


class DeviceListView(APIView):
    """GET /api/devices/ — périphériques visibles et droits de l'appelant.

    `paths` : chemins de lecture donnés par lab-media (I1) — le frontend les utilise
    tels quels, sans jamais les recomposer."""

    def get(self, request):
        inventory.sync()
        user = request.user
        out = []
        for device in devices_in_order():
            # v3 : admin compris, seuls les périphériques avec un Grant.
            rights = rights_for(user.email, device)
            if not has_any(rights):
                continue
            out.append({
                'slug': device.slug,
                'label': device.label,
                'online': _online(device),
                'has_audio': device.has_audio,
                'paths': device.paths(),
                **{cap: {'allowed': rights[cap].allowed, 'since': iso(rights[cap].since)}
                   for cap in CAPS},
            })
        return Response(out)


class GrantViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                   mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                   mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Droits par email — admins uniquement. PUT désactivé : PATCH seulement."""

    queryset = Grant.objects.select_related('device')
    permission_classes = [IsPeripheriquesAdmin]
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    @action(detail=False, methods=['get'], url_path='devices')
    def devices(self, request):
        """GET /api/grants/devices/ (v4) — TOUS les périphériques, pour le
        sélecteur de la page Droits, indépendamment de tout Grant. Sans elle, un
        admin sans Grant ne pouvait accorder aucun droit, même à lui-même.
        Route de liste : le SimpleRouter la déclare avant `grants/<pk>/`."""
        inventory.sync()
        return Response([
            {'slug': d.slug, 'label': d.label, 'online': _online(d),
             'has_audio': d.has_audio, 'state': d.state, 'connected': d.connected}
            for d in devices_in_order()
        ])

    def get_serializer_class(self):
        if self.action == 'create':
            return GrantCreateSerializer
        if self.action == 'partial_update':
            return GrantUpdateSerializer
        return GrantSerializer

    def perform_create(self, serializer):
        email = serializer.validated_data['email']
        try:
            exists = keycloak_admin.find_user_by_email(email) is not None
        except keycloak_admin.KeycloakAdminError as exc:
            log.error('Vérification Keycloak de %s impossible : %s', email, exc)
            raise KeycloakUnavailable() from exc
        if not exists:
            raise ValidationError({'email': [NO_ACCOUNT]})
        now = now_s()
        data = serializer.validated_data
        serializer.save(
            granted_by=self.request.user.email,
            video_since=now if data.get('video') else None,
            audio_since=now if data.get('audio') else None,
        )

    def partial_update(self, request, *args, **kwargs):
        grant = self.get_object()
        ser = GrantUpdateSerializer(grant, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        now = now_s()
        revoked = False
        with transaction.atomic():
            grant = Grant.objects.select_for_update().get(pk=grant.pk)
            for cap in CAPS:
                if cap not in ser.validated_data:
                    continue
                new = ser.validated_data[cap]
                old = getattr(grant, cap)
                if new and not old:
                    setattr(grant, f'{cap}_since', now)
                elif old and not new:
                    setattr(grant, f'{cap}_since', None)
                    revoked = True
                setattr(grant, cap, new)
            grant.granted_by = request.user.email
            grant.save()
        if revoked:
            kick_revoked(grant.email, grant.device)
        return Response(GrantSerializer(grant).data)

    def update(self, request, *args, **kwargs):  # PUT
        return self.http_method_not_allowed(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        grant = self.get_object()
        email, device = grant.email, grant.device
        grant.delete()
        kick_revoked(email, device)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MaterielView(APIView):
    """GET /api/admin/materiel/ (v6, admins) — catalogue I7 servi tel quel.

    Relu à chaque requête, sans cache : quelques ko, page consultée rarement,
    et un cache par processus serverait un contenu périmé après mise à jour.
    Le JSON est seulement validé syntaxiquement ; les octets du fichier sont
    renvoyés sans transformation.
    """

    permission_classes = [IsPeripheriquesAdmin]

    def get(self, request):
        try:
            with open(settings.MATERIEL_PATH, 'rb') as fh:
                raw = fh.read()
            json.loads(raw)
        except (OSError, ValueError) as exc:
            log.error('Catalogue matériel illisible (%s) : %s', settings.MATERIEL_PATH, exc)
            return Response({'detail': 'Catalogue matériel indisponible.'}, status=503)
        return HttpResponse(raw, content_type='application/json')


# ── Gestion des périphériques (chantier webcams, admins des droits) ───────────

LABEL_MAX = 60
LABEL_ERROR = "Nom d'affichage : de 1 à 60 caractères, sur une seule ligne."
MEDIA_DOWN = 'Serveur média indisponible.'
MEDIA_ODD = 'Réponse inattendue du serveur média.'


def admin_device(device: Device, grants: int | None = None) -> dict:
    return {
        'id': device.slug,
        'label': device.label,
        'model': device.model,
        'vendor_id': device.vendor_id,
        'product_id': device.product_id,
        'serial': device.serial,
        'usb_port': device.usb_port,
        'has_audio': device.has_audio,
        'connected': device.connected,
        'state': device.state,
        'error_detail': device.error_detail,
        'record': device.record,
        'first_seen': iso(device.first_seen),
        'last_seen': iso(device.last_seen),
        'paths': device.paths(),
        'listed': device.listed,
        'grants': device.grants.count() if grants is None else grants,
    }


def _inventory_status() -> dict:
    st = inventory.status()
    return {'reachable': st['reachable'], 'checked_at': iso(st['checked_at'])}


class AdminDeviceListView(APIView):
    """GET /api/admin/devices/ — tous les périphériques déjà vus, branchés ou non."""

    permission_classes = [IsPeripheriquesAdmin]

    def get(self, request):
        inventory.sync()
        rows = devices_in_order().annotate(n_grants=Count('grants'))
        return Response({
            'inventory': _inventory_status(),
            'items': [admin_device(d, d.n_grants) for d in rows],
        })


class AdminDeviceView(APIView):
    """/api/admin/devices/<id>/ — GET ; PATCH {label?, record?} ; DELETE (oubli).

    `record` est relayé à lab-media (le réglage vit là-bas, contrat §7) ; `label` est
    propre à l'app. L'oubli est relayé à lab-media, puis le périphérique et ses
    droits sont supprimés ici et toutes les lectures ouvertes sur lui sont expulsées."""

    permission_classes = [IsPeripheriquesAdmin]

    def _device(self, slug) -> Device | None:
        return Device.objects.filter(slug=slug).first()

    def get(self, request, slug):
        device = self._device(slug)
        if device is None:
            return Response({'detail': 'Périphérique inconnu.'}, status=404)
        return Response(admin_device(device))

    def patch(self, request, slug):
        device = self._device(slug)
        if device is None:
            return Response({'detail': 'Périphérique inconnu.'}, status=404)
        body = request.data if isinstance(request.data, dict) else None
        if not body or set(body) - {'label', 'record'}:
            return Response({'detail': 'Champs admis : label, record.'}, status=400)
        label = None
        if 'label' in body:
            label = body['label']
            if not isinstance(label, str) or inventory.clean_text(label, 10_000) != label.strip() \
                    or not 1 <= len(label.strip()) <= LABEL_MAX:
                return Response({'label': [LABEL_ERROR]}, status=400)
            label = label.strip()
        if 'record' in body and not isinstance(body['record'], bool):
            return Response({'record': ['Vrai ou faux attendu.']}, status=400)

        if 'record' in body:
            try:
                item = media_client.set_device_record(device.slug, body['record'])
            except media_client.DeviceRefused as exc:
                if exc.status == 409 and exc.code == 'legacy':
                    return Response({'record': ["L'ancienne webcam ne s'enregistre plus."]},
                                    status=409)
                if exc.status == 404:
                    return Response({'detail': 'Le serveur média ne connaît pas ce périphérique.'},
                                    status=404)
                log.warning('Réglage d\'enregistrement de %s refusé : %s', device.slug, exc)
                return Response({'detail': MEDIA_ODD}, status=502)
            except media_client.MediaError as exc:
                log.warning('Réglage d\'enregistrement de %s impossible : %s', device.slug, exc)
                return Response({'detail': MEDIA_DOWN}, status=503)
            device.record = item.get('record') is True
            inventory.invalidate()
        if label is not None:
            device.label = label
        device.save()
        return Response(admin_device(device))

    def delete(self, request, slug):
        device = self._device(slug)
        if device is None:
            return Response({'detail': 'Périphérique inconnu.'}, status=404)
        try:
            media_client.forget_device(device.slug)
        except media_client.DeviceRefused as exc:
            if exc.status == 409:
                return Response({'detail': 'Ce périphérique est branché : il ne peut pas être '
                                           'oublié. Débranchez-le d\'abord.'}, status=409)
            if exc.status != 404:       # 404 : déjà oublié par lab-media — on termine ici
                log.warning('Oubli de %s refusé : %s', device.slug, exc)
                return Response({'detail': MEDIA_ODD}, status=502)
        except media_client.MediaError as exc:
            log.warning('Oubli de %s impossible : %s', device.slug, exc)
            return Response({'detail': MEDIA_DOWN}, status=503)
        paths = [p for p in device.paths().values() if p]
        slug = device.slug
        device.delete()                  # ses droits partent avec lui (CASCADE)
        inventory.invalidate()
        kick_paths(paths, f'oubli de {slug}')
        log.info('Périphérique %s oublié par %s', slug, request.user.email)
        return Response(status=status.HTTP_204_NO_CONTENT)
