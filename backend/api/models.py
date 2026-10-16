from django.db import models
from django.db.models.functions import Lower, Trim


class Device(models.Model):
    """Périphérique de l'hôte tel que lab-media le décrit (contrat webcams, I0/I1).

    `slug` porte l'identifiant I0 calculé par lab-media (`usb-<vid>-<pid>-<s|p>…`,
    ou `webcam-ancienne` pour l'entrée héritée) : il ne change jamais pour un même
    appareil. Le nom d'affichage (`label`) est propre à l'app : `model` à la
    création, modifiable ensuite par les administrateurs des droits, jamais
    réécrit par la synchronisation.

    Les chemins MediaMTX (`path_*`) sont ceux que donne I1, jamais recomposés :
    le hook (I2), la relecture et l'expulsion retrouvent le périphérique par eux.
    Tout le reste est le dernier état connu de l'inventaire (`api.inventory`).
    """

    STATES = ('live', 'waiting', 'starting', 'error', 'offline', 'legacy')

    slug = models.SlugField(primary_key=True, max_length=64)
    label = models.CharField(max_length=100)
    model = models.CharField(max_length=100, blank=True, default='')
    vendor_id = models.CharField(max_length=4, blank=True, default='')
    product_id = models.CharField(max_length=4, blank=True, default='')
    serial = models.CharField(max_length=64, blank=True, default='')
    usb_port = models.CharField(max_length=64, blank=True, default='')
    has_audio = models.BooleanField(default=False)
    connected = models.BooleanField(default=False)
    state = models.CharField(max_length=20, blank=True, default='')
    error_detail = models.CharField(max_length=300, blank=True, default='')
    record = models.BooleanField(default=False)
    first_seen = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    path_av = models.CharField(max_length=120, blank=True, default='')
    path_video = models.CharField(max_length=120, blank=True, default='')
    path_audio = models.CharField(max_length=120, blank=True, default='')
    # Faux quand la dernière lecture réussie de l'inventaire ne le contenait plus.
    listed = models.BooleanField(default=True)

    class Meta:
        db_table = 'devices'
        ordering = ['slug']
        constraints = [
            # Deux périphériques ne portent jamais le même nom d'affichage, à la casse et aux
            # espaces de bord près (garantie en base : une course finit en IntegrityError,
            # que l'inventaire et le renommage savent traiter).
            models.UniqueConstraint(Lower(Trim('label')), name='device_label_unique_ci'),
        ]

    def __str__(self) -> str:
        return self.slug

    @classmethod
    def label_taken(cls, label: str, exclude: str | None = None) -> bool:
        """Nom déjà porté par un autre périphérique — même comparaison que la contrainte
        (`lower(trim(label))`, faite par la base)."""
        qs = cls.objects.annotate(_key=Lower(Trim('label'))).filter(
            _key=Lower(Trim(models.Value(label))))
        if exclude is not None:
            qs = qs.exclude(slug=exclude)
        return qs.exists()

    def paths(self) -> dict[str, str | None]:
        """Chemins connus, par kind ; None si absent (pas de micro)."""
        return {'av': self.path_av or None, 'video': self.path_video or None,
                'audio': self.path_audio or None}

    def kind_of(self, path: str) -> str | None:
        """Kind ('av' | 'video' | 'audio') du chemin `path` pour CE périphérique."""
        for kind, known in self.paths().items():
            if known and known == path:
                return kind
        return None

    @classmethod
    def by_path(cls, path: str) -> 'tuple[Device, str] | tuple[None, None]':
        """Retrouve le périphérique par un chemin exact parmi ses chemins connus.
        Chemin vide, inconnu ou ambigu (deux périphériques) ⇒ (None, None)."""
        if not isinstance(path, str) or not path:
            return None, None
        found = list(cls.objects.filter(
            models.Q(path_av=path) | models.Q(path_video=path) | models.Q(path_audio=path))[:2])
        if len(found) != 1:
            return None, None
        return found[0], found[0].kind_of(path)


class Grant(models.Model):
    """Droit d'un compte du realm (par email) sur un périphérique.

    `<cap>_since` est l'instant d'octroi de la capacité : il borne la relecture
    (un droit ne donne accès qu'aux enregistrements postérieurs à son octroi).
    Il est remis à zéro à chaque retrait puis ré-octroi.
    """

    email = models.EmailField(max_length=255)
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='grants')
    video = models.BooleanField(default=False)
    audio = models.BooleanField(default=False)
    video_since = models.DateTimeField(null=True, blank=True)
    audio_since = models.DateTimeField(null=True, blank=True)
    granted_by = models.EmailField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'grants'
        ordering = ['device_id', 'email']
        constraints = [
            models.UniqueConstraint(fields=['email', 'device'], name='grant_unique_email_device'),
        ]

    def __str__(self) -> str:
        return f'{self.email} → {self.device_id}'


class MediaSession(models.Model):
    """Lecture en direct autorisée par le hook, gardée pour pouvoir
    l'expulser (lab-media/API.md §1.2) quand le droit correspondant est retiré.

    Purge : une ligne disparaît quand son expulsion réussit, quand une révocation
    la trouve absente des sessions listées par lab-media (session déjà terminée),
    ou au bout de `SESSION_RETENTION` (filet de sécurité, cf. api.hook).
    """

    id = models.CharField(primary_key=True, max_length=100)
    email = models.EmailField(max_length=255, db_index=True)
    path = models.CharField(max_length=120)
    protocol = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'media_sessions'
