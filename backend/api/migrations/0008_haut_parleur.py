"""Chantier « haut-parleur » (contrat v1) : sorte de périphérique (`kind`), chemin de prise de
parole (`path_talk`, I1) et capacité « Parler » des droits (`speaker`, `speaker_since`).

Purement additive : toute ligne existante est une webcam sans chemin `talk` et sans droit
« Parler » — les défauts. Réversible (les colonnes sont retirées).
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('api', '0007_coupure_et_pont')]

    operations = [
        migrations.AddField(
            model_name='device',
            name='kind',
            field=models.CharField(default='webcam', max_length=10),
        ),
        migrations.AddField(
            model_name='device',
            name='path_talk',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='grant',
            name='speaker',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='grant',
            name='speaker_since',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
