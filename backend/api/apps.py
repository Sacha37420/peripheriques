from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'
    verbose_name = 'Peripheriques API'

    def ready(self):
        # v5 : jamais de `?key=` (hook MediaMTX) ni de `?ticket=` dans un log.
        from . import log_filters
        log_filters.install()
