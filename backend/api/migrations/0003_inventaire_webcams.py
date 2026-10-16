"""Chantier « webcams » : inventaire des périphériques (contrat v1, I0/I1).

Schéma : identifiant I0 jusqu'à 64 caractères, état d'inventaire et chemins connus.
Données : migration suivante (0004).
"""
from django.db import migrations, models


def field(name, f):
    return migrations.AddField(model_name='device', name=name, field=f)


class Migration(migrations.Migration):

    dependencies = [('api', '0002_seed_webcam')]

    operations = [
        migrations.AlterField(
            model_name='device', name='slug',
            field=models.SlugField(primary_key=True, serialize=False, max_length=64),
        ),
        field('model', models.CharField(max_length=100, blank=True, default='')),
        field('vendor_id', models.CharField(max_length=4, blank=True, default='')),
        field('product_id', models.CharField(max_length=4, blank=True, default='')),
        field('serial', models.CharField(max_length=64, blank=True, default='')),
        field('usb_port', models.CharField(max_length=64, blank=True, default='')),
        field('has_audio', models.BooleanField(default=False)),
        field('connected', models.BooleanField(default=False)),
        field('state', models.CharField(max_length=20, blank=True, default='')),
        field('error_detail', models.CharField(max_length=300, blank=True, default='')),
        field('record', models.BooleanField(default=False)),
        field('first_seen', models.DateTimeField(null=True, blank=True)),
        field('last_seen', models.DateTimeField(null=True, blank=True)),
        field('path_av', models.CharField(max_length=120, blank=True, default='')),
        field('path_video', models.CharField(max_length=120, blank=True, default='')),
        field('path_audio', models.CharField(max_length=120, blank=True, default='')),
        field('listed', models.BooleanField(default=True)),
    ]
