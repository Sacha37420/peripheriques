from rest_framework import serializers

from .access import iso
from .models import Device, Grant

NEED_ONE = 'Au moins un droit (vidéo ou micro) est requis.'
NEED_ONE_PATCH = 'Au moins un droit (vidéo ou micro) est requis. Pour tout retirer, utiliser DELETE.'
NO_ACCOUNT = 'Aucun compte du lab avec cet email.'
NO_MIC = "Ce périphérique n'a pas de micro : seul le droit Vidéo peut être accordé."
ALREADY = 'Un droit existe déjà pour cet email sur ce périphérique : utiliser PATCH.'


class GrantSerializer(serializers.ModelSerializer):
    device = serializers.SlugRelatedField(slug_field='slug', queryset=Device.objects.all())
    video_since = serializers.SerializerMethodField()
    audio_since = serializers.SerializerMethodField()
    updated_at = serializers.SerializerMethodField()

    class Meta:
        model = Grant
        fields = ['id', 'email', 'device', 'video', 'audio', 'video_since', 'audio_since',
                  'granted_by', 'updated_at']
        read_only_fields = ['id', 'granted_by']
        # L'unicité (email, device) est contrôlée à la main dans validate() pour
        # un message explicite (le validateur auto de DRF est retiré).
        validators = []

    def get_video_since(self, obj):
        return iso(obj.video_since)

    def get_audio_since(self, obj):
        return iso(obj.audio_since)

    def get_updated_at(self, obj):
        return iso(obj.updated_at)

    def validate_email(self, value: str) -> str:
        return value.strip().lower()


class GrantCreateSerializer(GrantSerializer):
    video = serializers.BooleanField(default=False)
    audio = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if not (attrs.get('video') or attrs.get('audio')):
            raise serializers.ValidationError({'non_field_errors': [NEED_ONE]})
        if attrs.get('audio') and not attrs['device'].has_audio:
            raise serializers.ValidationError({'audio': [NO_MIC]})
        if Grant.objects.filter(email=attrs['email'], device=attrs['device']).exists():
            raise serializers.ValidationError({'non_field_errors': [ALREADY]})
        return attrs


class GrantUpdateSerializer(serializers.ModelSerializer):
    """PATCH : seules les deux capacités sont modifiables."""

    class Meta:
        model = Grant
        fields = ['video', 'audio']

    def validate(self, attrs):
        if attrs.get('audio') and not self.instance.device.has_audio:
            raise serializers.ValidationError({'audio': [NO_MIC]})
        video = attrs.get('video', self.instance.video)
        audio = attrs.get('audio', self.instance.audio)
        if not (video or audio):
            raise serializers.ValidationError({'non_field_errors': [NEED_ONE_PATCH]})
        return attrs
