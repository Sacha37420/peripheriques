from rest_framework.permissions import BasePermission


class IsPeripheriquesAdmin(BasePermission):
    """Membres de PERIPHERIQUES_ADMIN_GROUPS : gestion des droits.

    Distinct de l'accès à l'app (« au moins un groupe », Verrou 2) : tout membre
    du lab entre, seuls les admins donnent des droits.
    """

    message = "Réservé aux administrateurs des périphériques."

    def has_permission(self, request, view) -> bool:
        return bool(getattr(request.user, 'is_admin', False))
