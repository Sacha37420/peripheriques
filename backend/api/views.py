import json
import logging

from django.conf import settings
from django.http import HttpResponse

from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import keycloak_admin, media_client
from .access import CAPS, has_any, iso, now_s, rights_for
from .kick import kick_revoked
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


class DeviceListView(APIView):
    """GET /api/devices/ — périphériques visibles et droits de l'appelant."""

    def get(self, request):
        user = request.user
        out = []
        for device in Device.objects.all():
            # v3 : admin compris, seuls les périphériques avec un Grant.
            rights = rights_for(user.email, device)
            if not has_any(rights):
                continue
            out.append({
                'slug': device.slug,
                'label': device.label,
                'online': media_client.path_ready(f'{device.slug}/av'),
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
        return Response([
            {'slug': d.slug, 'label': d.label,
             'online': media_client.path_ready(f'{d.slug}/av')}
            for d in Device.objects.all()
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
            kick_revoked(grant.email, grant.device_id)
        return Response(GrantSerializer(grant).data)

    def update(self, request, *args, **kwargs):  # PUT
        return self.http_method_not_allowed(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        grant = self.get_object()
        email, slug = grant.email, grant.device_id
        grant.delete()
        kick_revoked(email, slug)
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
