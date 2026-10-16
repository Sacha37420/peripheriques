"""Chantier « webcams » : l'unique ligne `webcam` devient `webcam-ancienne` (contrat v1, I0).

Chemins hérités `webcam/{av,video,audio}`, nom d'affichage « Ancienne webcam », jamais capturée,
enregistrement coupé. Ses droits sont gardés tels quels, `since` compris (seule la clé change).
Séparée du schéma (0003) : sous PostgreSQL, un ALTER TABLE après des écritures dans la même
transaction échoue (« pending trigger events »).
"""
from django.db import migrations

OLD, NEW = 'webcam', 'webcam-ancienne'
LEGACY = {
    'label': 'Ancienne webcam',
    'has_audio': True,
    'connected': False,
    'state': 'legacy',
    'record': False,
    'path_av': 'webcam/av',
    'path_video': 'webcam/video',
    'path_audio': 'webcam/audio',
    'listed': True,
}


def forwards(apps, schema_editor):
    Device = apps.get_model('api', 'Device')
    Grant = apps.get_model('api', 'Grant')
    if not Device.objects.filter(slug=OLD).exists():
        return
    Device.objects.update_or_create(slug=NEW, defaults=LEGACY)
    Grant.objects.filter(device_id=OLD).update(device_id=NEW)
    Device.objects.filter(slug=OLD).delete()


def backwards(apps, schema_editor):
    Device = apps.get_model('api', 'Device')
    Grant = apps.get_model('api', 'Grant')
    if not Device.objects.filter(slug=NEW).exists():
        return
    Device.objects.update_or_create(slug=OLD, defaults={'label': 'Webcam (vidéo + micro)'})
    Grant.objects.filter(device_id=NEW).update(device_id=OLD)
    Device.objects.filter(slug=NEW).delete()


class Migration(migrations.Migration):

    dependencies = [('api', '0003_inventaire_webcams')]

    operations = [migrations.RunPython(forwards, backwards)]
