from django.db import models


class Device(models.Model):
    """Périphérique de l'hôte publié par MediaMTX sous `<slug>/{video,audio,av}` (I1)."""

    slug = models.SlugField(primary_key=True, max_length=50)
    label = models.CharField(max_length=100)

    class Meta:
        db_table = 'devices'
        ordering = ['slug']

    def __str__(self) -> str:
        return self.slug


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
    """Lecture en direct autorisée par le hook (I2 §4), gardée pour pouvoir la
    couper (kick, I4) quand le droit correspondant est retiré.

    Purge : une ligne disparaît quand son kick réussit, quand une révocation la
    trouve absente de `/v3/{webrtc,hls}sessions/list` (session déjà terminée), ou au
    bout de `SESSION_RETENTION` (filet de sécurité, cf. api.hook).
    """

    id = models.CharField(primary_key=True, max_length=100)
    email = models.EmailField(max_length=255, db_index=True)
    path = models.CharField(max_length=120)
    protocol = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'media_sessions'
