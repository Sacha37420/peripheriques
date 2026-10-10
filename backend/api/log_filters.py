"""Masquage des secrets transportés en query string (contrat I2/I3, v5).

Deux URL portent un secret dans la query, imposé par leur appelant :
  * `/api/media-auth/?key=<MEDIA_HOOK_KEY>` — MediaMTX ne sait pas envoyer la
    clé du hook autrement (`authHTTPAddress`) ;
  * `/api/recordings/stream/?ticket=<signé>` — `<video src>` ne porte pas de header.

Le log d'accès (runserver : logger `django.server` ; gunicorn : `gunicorn.access`)
écrit la ligne de requête complète, donc ces secrets en clair. Ce filtre réécrit
tout enregistrement qui en contient en `key=***` / `ticket=***`, quel que soit
le logger. Il est posé par `ApiConfig.ready()` sur les loggers d'accès/requête
et sur leurs handlers (cf. `install`), sans toucher à leur configuration.
"""
from __future__ import annotations

import logging
import re

_SECRET_QS = re.compile(r'(?i)([?&](?:key|ticket)=)[^&\s"\']*')

# Loggers qui écrivent l'URL demandée (ou pourraient l'écrire).
ACCESS_LOGGERS = ('django.server', 'django.request', 'gunicorn.access', 'gunicorn.error')


def redact(text: str) -> str:
    return _SECRET_QS.sub(r'\1***', text)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — un message mal formé ne doit pas tomber ici
            return True
        clean = redact(message)
        if clean != message:
            record.msg, record.args = clean, ()
        # gunicorn.access passe aussi la requête dans les attributs du record.
        for attr in ('request_line', 'r', 'q'):
            value = getattr(record, attr, None)
            if isinstance(value, str):
                setattr(record, attr, redact(value))
        return True


_FILTER = RedactSecretsFilter()


def install() -> None:
    """Idempotent. Filtre sur le logger (enregistrements émis directement) ET sur
    ses handlers (enregistrements propagés depuis un logger enfant)."""
    targets = [logging.getLogger(name) for name in ACCESS_LOGGERS] + [logging.getLogger()]
    for logger in targets:
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
        for handler in logger.handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)
