"""Seed du seul périphérique connu à ce jour (contrat I1 : slug `webcam`)."""
from django.db import migrations


def seed(apps, schema_editor):
    Device = apps.get_model('api', 'Device')
    Device.objects.update_or_create(slug='webcam', defaults={'label': 'Webcam (vidéo + micro)'})


def unseed(apps, schema_editor):
    apps.get_model('api', 'Device').objects.filter(slug='webcam').delete()


class Migration(migrations.Migration):

    dependencies = [('api', '0001_initial')]

    operations = [migrations.RunPython(seed, unseed)]
