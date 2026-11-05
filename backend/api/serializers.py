from rest_framework import serializers

from .access import iso
from .models import Device, Grant

NEED_ONE = 'Au moins un droit (vidéo ou micro) est requis.'
NEED_ONE_PATCH = 'Au moins un droit (vidéo ou micro) est requis. Pour tout retirer, utiliser DELETE.'
NO_ACCOUNT = 'Aucun compte du lab avec cet email.'
NO_MIC = "Ce périphérique n'a pas de micro : seul le droit Vidéo peut être accordé."
ALREADY = 'Un droit existe déjà pour cet email sur ce périphérique : utiliser PATCH.'
# Contrat haut-parleur : « Parler » sur un haut-parleur seulement, et seulement « Parler ».
NEED_SPEAKER = 'Le droit Parler est requis sur un haut-parleur.'
NEED_SPEAKER_PATCH = ('Le droit Parler est requis sur un haut-parleur. '
                      'Pour tout retirer, utiliser DELETE.')
SPEAKER_ONLY = "Un haut-parleur ne se regarde ni ne s'écoute : seul le droit Parler peut être accordé."
NOT_A_SPEAKER = "Ce périphérique n'est pas un haut-parleur : le droit Parler ne peut pas être accordé."


def check_caps(device, video: bool, audio: bool, speaker: bool, patch: bool) -> None:
    """Capacités admises sur `device` ; ValidationError (400) sinon."""
    if device.is_speaker:
        errors = {}
        if video:
            errors['video'] = [SPEAKER_ONLY]
        if audio:
            errors['audio'] = [SPEAKER_ONLY]
        if errors:
            raise serializers.ValidationError(errors)
        if not speaker:
            raise serializers.ValidationError(
                {'non_field_errors': [NEED_SPEAKER_PATCH if patch else NEED_SPEAKER]})
        return
    if speaker:
        raise serializers.ValidationError({'speaker': [NOT_A_SPEAKER]})
    if audio and not device.has_audio:
        raise serializers.ValidationError({'audio': [NO_MIC]})
    if not (video or audio):
        raise serializers.ValidationError(
            {'non_field_errors': [NEED_ONE_PATCH if patch else NEED_ONE]})


class GrantSerializer(serializers.ModelSerializer):
    device = serializers.SlugRelatedField(slug_field='slug', queryset=Device.objects.all())
    video_since = serializers.SerializerMethodField()
    audio_since = serializers.SerializerMethodField()
    speaker_since = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Grant
        fields = ['id', 'email', 'device', 'video', 'audio', 'speaker', 'video_since',
                  'audio_since', 'speaker_since', 'granted_by', 'updated_at']
        read_only_fields = ['id', 'granted_by']
        # L'unicité (email, device) est contrôlée à la main dans validate() pour
        # un message explicite (le validateur auto de DRF est retiré).
        validators = []

    def get_video_since(self, obj):
        return iso(obj.video_since)

    def get_audio_since(self, obj):
        return iso(obj.audio_since)

    def get_speaker_since(self, obj):
        return iso(obj.speaker_since)

    def get_updated_at(self, obj):
        return iso(obj.updated_at)

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class GrantCreateSerializer(GrantSerializer):
    video = serializers.BooleanField(default=False)
    audio = serializers.BooleanField(default=False)
    speaker = serializers.BooleanField(default=False)

    def validate(self, attrs):
        check_caps(attrs['device'], bool(attrs.get('video')), bool(attrs.get('audio')),
                   bool(attrs.get('speaker')), patch=False)
        if Grant.objects.filter(email=attrs['email'], device=attrs['device']).exists():
            raise serializers.ValidationError({'non_field_errors': [ALREADY]})
        return attrs


class GrantUpdateSerializer(serializers.ModelSerializer):
    """PATCH : seules les capacités sont modifiables."""

    class Meta:
        model = Grant
        fields = ['video', 'audio', 'speaker']

    def validate(self, attrs):
        g = self.instance
        device = g.device
        # Capacités AJOUTÉES par cette demande, contrôlées contre la sorte du périphérique.
        if device.is_speaker:
            errors = {cap: [SPEAKER_ONLY] for cap in ('video', 'audio') if attrs.get(cap)}
            if errors:
                raise serializers.ValidationError(errors)
        elif attrs.get('speaker'):
            raise serializers.ValidationError({'speaker': [NOT_A_SPEAKER]})
        if attrs.get('audio') and not device.has_audio and not device.is_speaker:
            raise serializers.ValidationError({'audio': [NO_MIC]})
        # Il doit rester au moins la capacité propre à la sorte du périphérique.
        if device.is_speaker:
            if not attrs.get('speaker', g.speaker):
                raise serializers.ValidationError({'non_field_errors': [NEED_SPEAKER_PATCH]})
        elif not (attrs.get('video', g.video) or attrs.get('audio', g.audio)):
            raise serializers.ValidationError({'non_field_errors': [NEED_ONE_PATCH]})
        return attrs
