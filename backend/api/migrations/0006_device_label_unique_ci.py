"""Chantier « webcams » : unicité du nom d'affichage, à la casse et aux espaces de bord près."""
import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('api', '0005_noms_uniques')]

    operations = [
        migrations.AddConstraint(
            model_name='device',
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower(django.db.models.functions.text.Trim('label')),
                name='device_label_unique_ci'),
        ),
    ]
