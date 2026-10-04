import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Device',
            fields=[
                ('slug', models.SlugField(primary_key=True, serialize=False, max_length=50)),
                ('label', models.CharField(max_length=100)),
            ],
            options={'db_table': 'devices', 'ordering': ['slug']},
        ),
        migrations.CreateModel(
            name='Grant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('email', models.EmailField(max_length=255)),
                ('video', models.BooleanField(default=False)),
                ('audio', models.BooleanField(default=False)),
                ('video_since', models.DateTimeField(blank=True, null=True)),
                ('audio_since', models.DateTimeField(blank=True, null=True)),
                ('granted_by', models.EmailField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='grants', to='api.device')),
            ],
            options={'db_table': 'grants', 'ordering': ['device_id', 'email']},
        ),
        migrations.AddConstraint(
            model_name='grant',
            constraint=models.UniqueConstraint(fields=('email', 'device'), name='grant_unique_email_device'),
        ),
        migrations.CreateModel(
            name='MediaSession',
            fields=[
                ('id', models.CharField(max_length=100, primary_key=True, serialize=False)),
                ('email', models.EmailField(db_index=True, max_length=255)),
                ('path', models.CharField(max_length=120)),
                ('protocol', models.CharField(blank=True, max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('last_seen', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'media_sessions'},
        ),
    ]
