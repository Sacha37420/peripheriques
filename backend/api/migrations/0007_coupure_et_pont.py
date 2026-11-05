"""Chantier « webcams-modeles » : coupure globale (I0) et sessions ouvertes par le pont (I2)."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('api', '0006_device_label_unique_ci')]

    operations = [
        migrations.AddField(
            model_name='mediasession',
            name='bridge',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='mediasession',
            name='viewer_email',
            field=models.EmailField(blank=True, default='', max_length=255),
        ),
        migrations.CreateModel(
            name='Cutoff',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, primary_key=True, serialize=False)),
                ('active', models.BooleanField(default=False)),
                ('since', models.DateTimeField(blank=True, null=True)),
                ('by', models.EmailField(blank=True, default='', max_length=255)),
                ('paused', models.JSONField(blank=True, default=list)),
                ('failures', models.JSONField(blank=True, default=list)),
                ('media_errors', models.JSONField(blank=True, default=list)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'cutoff'},
        ),
    ]
