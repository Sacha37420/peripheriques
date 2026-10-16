"""Chantier « webcams » : rend uniques les noms d'affichage avant la contrainte (0006).

Comparaison à la casse et aux espaces de bord près. Ordre déterministe (`first_seen`, puis
identifiant) : le premier garde son nom, les suivants reçoivent « 2 », « 3 »… Les noms sont
aussi débarrassés de leurs espaces de bord. Sans retour arrière à faire : un nom numéroté
reste valide sans la contrainte.
"""
from datetime import datetime, timezone

from django.db import migrations


def numbered(base: str, n: int, limit: int = 100) -> str:
    if n <= 1:
        return base[:limit]
    suffix = f' {n}'
    return base[:limit - len(suffix)].rstrip() + suffix


def forwards(apps, schema_editor):
    Device = apps.get_model('api', 'Device')
    early = datetime.min.replace(tzinfo=timezone.utc)   # jamais daté = le plus ancien (héritée)
    devices = sorted(Device.objects.all(), key=lambda d: (d.first_seen or early, d.slug))
    taken: set[str] = set()
    for d in devices:
        base = d.label.strip() or d.slug
        n = 1
        while numbered(base, n).strip().lower() in taken:
            n += 1
        label = numbered(base, n)
        taken.add(label.strip().lower())
        if label != d.label:
            Device.objects.filter(slug=d.slug).update(label=label)


class Migration(migrations.Migration):

    dependencies = [('api', '0004_webcam_ancienne')]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
