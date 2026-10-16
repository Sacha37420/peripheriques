"""Faux lab-media-auth pour les tests : un vrai serveur HTTP local qui rend
EXACTEMENT les formes de `lab-media/API.md` §1 (codes, enveloppes, champs,
erreurs `{"error", "detail"}`), limité aux chemins de l'app comme le vrai.

Le client (`api.media_client`) est donc exercé de bout en bout — URL, Basic,
paramètres, analyse des réponses — sans simuler `requests`.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

CONTROL = ('backend', 'int-secret')          # = settings_test MEDIA_INTERNAL_USER/PASSWORD
OWN_PATHS = ('webcam/av', 'webcam/video', 'webcam/audio')
PROTOCOLS = ('webrtc', 'hls', 'rtsp')


def device_item(dev_id='usb-32e4-9230-p3-4', model='HD USB Camera', has_audio=False,
                connected=True, state='live', record=False, usb_port='3-4', serial=''):
    """Objet de `GET /v1/devices` (contrat webcams v1, I1) — recopié de l'exemple du contrat."""
    vid, pid = dev_id.split('-')[1:3] if dev_id.startswith('usb-') else ('', '')
    return {
        'id': dev_id, 'kind': 'webcam', 'model': model,
        'vendor_id': vid, 'product_id': pid, 'serial': serial, 'usb_port': usb_port,
        'has_audio': has_audio, 'connected': connected, 'state': state, 'record': record,
        'first_seen': '2026-10-17T00:21:04+02:00', 'last_seen': '2026-10-17T09:12:40+02:00',
        'paths': {'av': f'webcam/{dev_id}/av', 'video': f'webcam/{dev_id}/video',
                  'audio': f'webcam/{dev_id}/audio' if has_audio else None},
    }


LEGACY_ITEM = {
    'id': 'webcam-ancienne', 'kind': 'webcam', 'model': 'USB PHY 2.0',
    'vendor_id': '', 'product_id': '', 'serial': '', 'usb_port': '',
    'has_audio': True, 'connected': False, 'state': 'legacy', 'record': False,
    'first_seen': '2026-10-04T10:00:00+02:00', 'last_seen': '2026-10-15T07:00:00+02:00',
    'paths': {'av': 'webcam/av', 'video': 'webcam/video', 'audio': 'webcam/audio'},
}


def path_item(path, ready=True):
    """Objet de `GET /v1/paths/<chemin>` (API.md §1.1)."""
    kind = path.rsplit('/', 1)[-1]
    tracks = {'av': ['H264', 'Opus'], 'video': ['H264'], 'audio': ['Opus']}[kind]
    return {'path': path, 'ready': ready,
            'readyTime': '2026-10-11T01:55:44.232874881Z' if ready else None,
            'tracks': tracks if ready else [], 'readers': 0,
            'inboundBytes': 348844 if ready else 0,
            'source': 'publisher', 'recorded': kind == 'av'}


def session_item(protocol, sid, path='webcam/av', state='read'):
    """Élément de `GET /v1/sessions` (API.md §1.2)."""
    return {'protocol': protocol, 'id': sid, 'path': path, 'state': state,
            'created': '2026-10-11T01:56:48.001168818Z', 'remoteAddr': '192.168.48.3:54626',
            'inboundBytes': 0, 'outboundBytes': 409}


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Un client qui ferme tôt (flux abandonné) n'est pas une erreur du faux serveur.
        import sys
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


class FakeLabMedia:
    def __init__(self):
        self.ready = {p: True for p in OWN_PATHS}
        self.devices: list[dict] = [dict(LEGACY_ITEM)]   # inventaire I1
        self.devices_status: int | None = None            # force un code sur GET /v1/devices
        self.sessions: list[dict] = []
        self.recordings: dict[str, list[dict]] = {}
        self.clip = b''
        self.garbage = False          # réponses 200 non-JSON
        self.calls: list[tuple] = []  # (méthode, chemin, query dict, authentifié ?)
        self.bodies: list = []        # corps JSON reçus (PATCH)
        fake = self

        def own_paths():
            out = set(OWN_PATHS)
            for d in fake.devices:
                out |= {p for p in d['paths'].values() if p}
            return out

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def _json(self, code, data, extra=None):
                raw = b'<html>pas du json</html>' if (fake.garbage and code == 200) else json.dumps(data).encode()
                self.send_response(code)
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _err(self, code, error, detail='…'):
                self._json(code, {'error': error, 'detail': detail})

            def _handle(self, method):
                url = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                length = int(self.headers.get('Content-Length') or 0)
                raw_body = self.rfile.read(length) if length else b''
                try:
                    body = json.loads(raw_body) if raw_body else None
                except ValueError:
                    body = ValueError
                expected = 'Basic ' + base64.b64encode(':'.join(CONTROL).encode()).decode()
                authed = self.headers.get('Authorization') == expected
                fake.calls.append((method, url.path, query, authed))
                if not authed:
                    return self._json(401, {'error': 'unauthorized', 'detail': 'Identifiants de contrôle requis.'},
                                      {'WWW-Authenticate': 'Basic realm="lab-media"'})
                parts = [p for p in url.path.split('/') if p]
                if parts[:2] == ['v1', 'paths'] and len(parts) > 2 and method == 'GET':
                    path = '/'.join(parts[2:])
                    if path not in own_paths():
                        return self._err(404, 'unknown_path')
                    return self._json(200, path_item(path, fake.ready.get(path, True)))
                if parts == ['v1', 'devices'] and method == 'GET':
                    if fake.devices_status:
                        return self._err(fake.devices_status, 'media_unavailable')
                    return self._json(200, fake.devices)
                if len(parts) == 3 and parts[:2] == ['v1', 'devices']:
                    dev = next((d for d in fake.devices if d['id'] == parts[2]), None)
                    if dev is None:
                        return self._err(404, 'unknown_device')
                    if method == 'GET':
                        return self._json(200, dev)
                    if method == 'PATCH':
                        fake.bodies.append(body)
                        if not isinstance(body, dict) or set(body) != {'record'} \
                                or not isinstance(body['record'], bool):
                            return self._err(400, 'bad_request')
                        if dev['state'] == 'legacy':
                            return self._err(409, 'legacy')
                        dev['record'] = body['record']
                        return self._json(200, dev)
                    if method == 'DELETE':
                        if dev['connected']:
                            return self._err(409, 'connected')
                        fake.devices.remove(dev)
                        self.send_response(204)
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                        return None
                if parts == ['v1', 'sessions'] and method == 'GET':
                    protocol = query.get('protocol')
                    if protocol is not None and protocol not in PROTOCOLS:
                        return self._err(400, 'bad_protocol')
                    items = [s for s in fake.sessions if protocol in (None, s['protocol'])]
                    return self._json(200, {'items': items})
                if len(parts) == 5 and parts[:2] == ['v1', 'sessions'] and parts[4] == 'kick' and method == 'POST':
                    protocol, sid = parts[2], parts[3]
                    if protocol not in PROTOCOLS:
                        return self._err(400, 'bad_protocol')
                    mine = next((s for s in fake.sessions if s['protocol'] == protocol and s['id'] == sid), None)
                    if mine is None:
                        return self._err(404, 'unknown_session')
                    if mine['state'] == 'publish':
                        return self._err(409, 'not_a_reader')
                    fake.sessions.remove(mine)
                    self.send_response(204)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return None
                if parts == ['v1', 'recordings'] and method == 'GET':
                    if query.get('path') not in own_paths():
                        return self._err(404, 'unknown_path')
                    return self._json(200, fake.recordings.get(query['path'], []))
                if parts == ['v1', 'recordings', 'get'] and method == 'GET':
                    if query.get('path') not in own_paths():
                        return self._err(404, 'unknown_path')
                    if not fake.clip:
                        return self._err(404, 'no_recording')
                    # Comme le vrai : longueur inconnue, la connexion se ferme à la fin.
                    self.close_connection = True
                    self.send_response(200)
                    self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(fake.clip)
                    return None
                return self._err(404, 'not_found')

            def do_GET(self):
                self._handle('GET')

            def do_POST(self):
                self._handle('POST')

            def do_PATCH(self):
                self._handle('PATCH')

            def do_DELETE(self):
                self._handle('DELETE')

        self.httpd = _QuietServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def kicks(self) -> list[str]:
        return [path for method, path, _q, _a in self.calls
                if method == 'POST' and path.endswith('/kick')]


def closed_port_url() -> str:
    """URL d'un port local où rien n'écoute (lab-media injoignable)."""
    import socket
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return f'http://127.0.0.1:{port}'
