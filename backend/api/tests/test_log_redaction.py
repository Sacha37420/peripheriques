"""v5 : aucun secret de query string (`?key=`, `?ticket=`) dans les logs."""
import io
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from django.conf import settings
from django.test import SimpleTestCase

from api import log_filters

KEY = 'hook-key-for-tests'


class _Capture:
    def __init__(self, logger_name):
        self.logger = logging.getLogger(logger_name)
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)

    def __enter__(self):
        self.old_level = self.logger.level
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)
        return self.stream

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.old_level)


class RedactFilterTests(SimpleTestCase):
    def test_installed_on_access_loggers(self):
        for name in log_filters.ACCESS_LOGGERS:
            self.assertIn(log_filters._FILTER, logging.getLogger(name).filters, name)

    def test_runserver_access_line(self):
        # Même appel que django.core.servers.basehttp.WSGIRequestHandler.log_message.
        line = f'POST /api/media-auth/?key={KEY} HTTP/1.1'
        with _Capture('django.server') as out:
            logging.getLogger('django.server').info('"%s" %s %s', line, '200', '0',
                                                   extra={'status_code': 200})
        self.assertNotIn(KEY, out.getvalue())
        self.assertIn('"POST /api/media-auth/?key=*** HTTP/1.1" 200 0', out.getvalue())

    def test_ticket_and_other_params(self):
        line = 'GET /api/recordings/stream/?a=1&ticket=eyJ.abc:def&b=2 HTTP/1.1'
        with _Capture('django.server') as out:
            logging.getLogger('django.server').info('"%s" %s %s', line, '200', '10')
        self.assertIn('?a=1&ticket=***&b=2', out.getvalue())
        self.assertNotIn('eyJ.abc', out.getvalue())

    def test_gunicorn_access_record(self):
        with _Capture('gunicorn.access') as out:
            logging.getLogger('gunicorn.access').info(
                '%s', f'"POST /api/media-auth/?key={KEY} HTTP/1.1" 401',
                extra={'r': f'POST /api/media-auth/?key={KEY} HTTP/1.1', 'q': f'key={KEY}'})
        self.assertNotIn(KEY, out.getvalue())

    def test_prod_logging_config_filters_console(self):
        # settings_test remplace LOGGING : on vérifie la config de PROD elle-même.
        from config import settings as prod
        cfg = prod.LOGGING
        self.assertEqual(cfg['handlers']['console']['filters'], ['redact'])
        self.assertEqual(cfg['filters']['redact']['()'], 'api.log_filters.RedactSecretsFilter')
        # Sans dictConfig global (il reconfigurerait le logging des autres tests) :
        # même handler + même filtre, construits à la main.
        from django.utils.module_loading import import_string
        handler = logging.StreamHandler(io.StringIO())
        handler.addFilter(import_string(cfg['filters']['redact']['()'])())
        logger = logging.getLogger('api.test-redact')
        logger.addHandler(handler)
        try:
            logger.warning('appel %s', f'/api/media-auth/?key={KEY}')
        finally:
            logger.removeHandler(handler)
        self.assertIn('key=***', handler.stream.getvalue())
        self.assertNotIn(KEY, handler.stream.getvalue())


class RunserverEndToEndTests(SimpleTestCase):
    """Vrai `manage.py runserver` (le CMD du Dockerfile), sortie capturée."""

    def test_no_key_nor_ticket_in_runserver_output(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings_test', 'PYTHONUNBUFFERED': '1'}
        proc = subprocess.Popen(
            [sys.executable, 'manage.py', 'runserver', '--noreload', f'127.0.0.1:{port}'],
            cwd=settings.BASE_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buf = bytearray()

        def _read():
            for chunk in iter(lambda: proc.stdout.read1(4096), b''):
                buf.extend(chunk)

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        try:
            base = f'http://127.0.0.1:{port}'
            for _ in range(100):
                try:
                    urllib.request.urlopen(f'{base}/api/media-auth/', timeout=1)
                except urllib.error.HTTPError:
                    break
                except OSError:
                    time.sleep(0.1)
            for url, data in (
                (f'{base}/api/media-auth/?key={KEY}', b'{"action":"publish","user":"x","password":"y","path":"webcam/av"}'),
                (f'{base}/api/media-auth/?key={KEY}&x=1', b'garbage'),
                (f'{base}/api/media-auth/?key=wrong-{KEY}', b'{}'),
                (f'{base}/api/recordings/stream/?ticket=SECRETTICKET123', None),
            ):
                req = urllib.request.Request(url, data=data,
                                             headers={'Content-Type': 'application/json'})
                try:
                    urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError:
                    pass
            # La ligne d'accès est écrite APRÈS l'envoi de la réponse : attendre
            # qu'elle soit sortie avant d'arrêter le serveur (sinon test instable).
            deadline = time.time() + 10
            while time.time() < deadline and b'/api/recordings/stream/' not in bytes(buf):
                time.sleep(0.05)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            reader.join(timeout=5)
        output = bytes(buf).decode()
        self.assertIn('/api/media-auth/?key=***', output)        # la ligne d'accès existe…
        self.assertIn('/api/recordings/stream/?ticket=***', output)
        self.assertNotIn(KEY, output)                            # …sans le secret
        self.assertNotIn('SECRETTICKET123', output)
