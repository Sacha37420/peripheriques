"""Calcul des droits — seule source de vérité pour l'API REST, le hook (I2),
la relecture (I3) et la décision de kick (I4).

Vocabulaire :
  capacité (cap) : 'video' | 'audio' — ce qu'un Grant accorde ;
  kind           : 'video' | 'audio' | 'av' — suffixe d'un chemin MediaMTX (I1) ;
  since          : instant d'octroi d'une capacité, borne basse de la relecture.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import Device, Grant

CAPS = ('video', 'audio')
KIND_CAPS: dict[str, frozenset[str]] = {
    'video': frozenset({'video'}),
    'audio': frozenset({'audio'}),
    'av': frozenset({'video', 'audio'}),
}


@dataclass(frozen=True)
class Right:
    allowed: bool
    since: datetime | None  # toujours renseigné quand allowed (None ⇔ refusé)


Rights = dict[str, Right]

NO_RIGHT = Right(False, None)


def rights_for(email: str, device: Device | str) -> Rights:
    """Droits courants de `email` sur `device` (relus en base à chaque appel).

    Seul un Grant compte (contrat v3) : être admin (PERIPHERIQUES_ADMIN_GROUPS)
    permet de GÉRER les droits, jamais de voir/écouter ni de relire. Un admin
    qui veut voir se donne un Grant, soumis au même `since` que tout le monde.
    """
    slug = device.slug if isinstance(device, Device) else device
    grant = Grant.objects.filter(email=(email or '').lower(), device_id=slug).first()
    if grant is None:
        return {cap: NO_RIGHT for cap in CAPS}
    out: Rights = {}
    for cap in CAPS:
        since = getattr(grant, f'{cap}_since')
        # Fail closed : un droit sans instant d'octroi n'ouvrirait aucune borne
        # de relecture — il est traité comme absent.
        out[cap] = Right(True, since) if getattr(grant, cap) and since is not None else NO_RIGHT
    return out


def has_any(rights: Rights) -> bool:
    return any(r.allowed for r in rights.values())


def kind_allowed(rights: Rights, kind: str) -> bool:
    """Lecture en direct de `<slug>/<kind>` : toutes les capacités du kind requises."""
    caps = KIND_CAPS.get(kind)
    return bool(caps) and all(rights[c].allowed for c in caps)


def tracks_at(rights: Rights, instant: datetime) -> list[str]:
    """Pistes relisibles à partir de `instant` (since ≤ instant), ordre stable."""
    return [
        cap for cap in CAPS
        if rights[cap].allowed and rights[cap].since is not None and rights[cap].since <= instant
    ]


def parse_path(path: str) -> tuple[str, str] | None:
    """'webcam/av' (ou '/webcam/av') → ('webcam', 'av') ; None si hors I1."""
    parts = (path or '').strip('/').split('/')
    if len(parts) != 2 or parts[1] not in KIND_CAPS or not parts[0]:
        return None
    return parts[0], parts[1]


# ── Horodatages (convention commune du contrat : ISO 8601 UTC avec `Z`) ──────

def now_s() -> datetime:
    """Maintenant, tronqué à la seconde : un `since` exposé à la seconde près
    doit être exactement celui stocké, sinon un client qui relit à partir du
    `since` affiché se ferait refuser pour quelques microsecondes."""
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_iso(value) -> datetime:
    """Analyse un ISO 8601 **avec fuseau** ; ValueError sinon."""
    if not isinstance(value, str):
        raise ValueError('horodatage attendu')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('fuseau requis (ex. 2026-10-04T12:00:00Z)')
    return dt.astimezone(timezone.utc)
