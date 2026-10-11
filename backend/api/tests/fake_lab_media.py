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


def path_item(path, ready=True):
    """Objet de `GET /v1/paths/<chemin>` (API.md §1.1)."""
    tracks = {'webcam/av': ['H264', 'Opus'], 'webcam/video': ['H264'], 'webcam/audio': ['Opus']}[path]
    return {'path': path, 'ready': ready,
            'readyTime': '2026-10-11T01:55:44.232874881Z' if ready else None,
            'tracks': tracks if ready else [], 'readers': 0,
            'inboundBytes': 348844 if ready else 0,
            'source': 'publisher', 'recorded': path == 'webcam/av'}


def session_item(protocol, sid, path='webcam/av', state='read'):
    """Élément de `GET /v1/sessions` (API.md §1.2)."""
    return {'protocol': protocol, 'id': sid, 'path': path, 'state': state,
            'created': '2026-10-11T01:56:48.001168818Z', 'remoteAddr': '192.168.48.3:54626',
            'inboundBytes': 0, 'outboundBytes': 409}


class FakeLabMedia:
    def __init__(self):
        self.ready = {p: True for p in OWN_PATHS}
        self.sessions: list[dict] = []
        self.recordings: dict[str, list[dict]] = {}
        self.clip = b''
        self.garbage = False          # réponses 200 non-JSON
        self.calls: list[tuple] = []  # (méthode, chemin, query dict, authentifié ?)
        fake = self

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
                if length:
                    self.rfile.read(length)
                expected = 'Basic ' + base64.b64encode(':'.join(CONTROL).encode()).decode()
                authed = self.headers.get('Authorization') == expected
                fake.calls.append((method, url.path, query, authed))
                if not authed:
                    return self._json(401, {'error': 'unauthorized', 'detail': 'Identifiants de contrôle requis.'},
                                      {'WWW-Authenticate': 'Basic realm="lab-media"'})
                parts = [p for p in url.path.split('/') if p]
                if parts[:2] == ['v1', 'paths'] and len(parts) > 2 and method == 'GET':
                    path = '/'.join(parts[2:])
                    if path not in OWN_PATHS:
                        return self._err(404, 'unknown_path')
                    return self._json(200, path_item(path, fake.ready[path]))
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
                    if query.get('path') not in OWN_PATHS:
                        return self._err(404, 'unknown_path')
                    return self._json(200, fake.recordings.get(query['path'], []))
                if parts == ['v1', 'recordings', 'get'] and method == 'GET':
                    if query.get('path') not in OWN_PATHS:
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

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def kicks(self) -> list[str]:
        return [path for method, path, _q, _a in self.calls if method == 'POST']


def closed_port_url() -> str:
    """URL d'un port local où rien n'écoute (lab-media injoignable)."""
    import socket
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return f'http://127.0.0.1:{port}'
