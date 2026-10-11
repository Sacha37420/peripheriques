"""Le backend face à lab-media (lab-media/API.md §1) — client et parcours complets.

Un faux lab-media-auth (vrai serveur HTTP, formes exactes d'API.md) remplace la
brique. Cas couverts : nominal, brique injoignable, identifiants de contrôle
refusés, expulsion des deux protocoles, relecture clippée au `since`.

Règle de sûreté vérifiée partout : aucune panne de la brique n'ouvre quoi que ce
soit — périphérique « hors ligne », relecture en 503/502, révocation acquise.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from django.test import TestCase, override_settings

from api import keycloak_admin, media_client
from api.models import Grant, MediaSession
from unittest import mock

from .fake_lab_media import FakeLabMedia, closed_port_url, session_item
from .helpers import ADMIN, MEMBER, admin_token, bearer, make_token, patch_jwks

HOOK = '/api/media-auth/?key=hook-key-for-tests'


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class FakeBrickCase(TestCase):
    def setUp(self):
        self.fake = FakeLabMedia()
        self.addCleanup(self.fake.close)
        override = override_settings(MEDIA_API_URL=self.fake.url)
        override.enable()
        self.addCleanup(override.disable)
        jwks = patch_jwks()
        jwks.start()
        self.addCleanup(jwks.stop)
        self.member = bearer(make_token(MEMBER))
        self.admin = bearer(admin_token())

    def grant(self, email=MEMBER, video_since=None, audio_since=None):
        return Grant.objects.create(
            email=email, device_id='webcam',
            video=video_since is not None, audio=audio_since is not None,
            video_since=video_since, audio_since=audio_since, granted_by=ADMIN)

    def down(self):
        """lab-media injoignable."""
        return override_settings(MEDIA_API_URL=closed_port_url())

    def bad_creds(self):
        """lab-media refuse nos identifiants de contrôle."""
        return override_settings(MEDIA_INTERNAL_PASSWORD='pas-le-bon')


class ClientTests(FakeBrickCase):
    """`api.media_client` contre les formes exactes d'API.md."""

    def test_path_ready(self):
        self.assertIs(media_client.path_ready('webcam/av'), True)
        method, path, query, authed = self.fake.calls[-1]
        self.assertEqual((method, path, authed), ('GET', '/v1/paths/webcam/av', True))
        self.fake.ready['webcam/av'] = False
        self.assertIs(media_client.path_ready('webcam/av'), False)
        # 404 unknown_path : chemin non déclaré, ou hors de nos préfixes
        self.assertIs(media_client.path_ready('autre/av'), False)
        self.fake.garbage = True
        self.assertIs(media_client.path_ready('webcam/video'), False)

    def test_path_ready_never_raises_when_brick_fails(self):
        with self.down():
            self.assertIs(media_client.path_ready('webcam/av'), False)
        with self.bad_creds(), self.assertLogs('api.media', level='ERROR') as logs:
            self.assertIs(media_client.path_ready('webcam/av'), False)
        self.assertIn('identifiants de contrôle', logs.output[0])
        self.assertNotIn('pas-le-bon', ''.join(logs.output))

    def test_list_sessions(self):
        self.fake.sessions = [session_item('hls', 'h1'), session_item('webrtc', 'w1', 'webcam/video'),
                              session_item('rtsp', 'r1', state='publish')]
        self.assertEqual(media_client.list_sessions('hls'), [session_item('hls', 'h1')])
        self.assertEqual(self.fake.calls[-1][:3], ('GET', '/v1/sessions', {'protocol': 'hls'}))
        self.assertEqual([s['id'] for s in media_client.list_sessions('webrtc')], ['w1'])
        with self.assertRaises(KeyError):
            media_client.list_sessions('rtsp')      # l'app n'expulse que webrtc et hls
        self.fake.garbage = True
        with self.assertRaises(media_client.MediaError):
            media_client.list_sessions('hls')

    def test_kick(self):
        self.fake.sessions = [session_item('hls', 'h1'), session_item('webrtc', 'w1'),
                              session_item('webrtc', 'pub', state='publish')]
        media_client.kick_session('hls', 'h1')              # 204
        media_client.kick_session('webrtc', 'w1')           # 204
        self.assertEqual(self.fake.kicks(), ['/v1/sessions/hls/h1/kick', '/v1/sessions/webrtc/w1/kick'])
        self.assertEqual([s['id'] for s in self.fake.sessions], ['pub'])
        media_client.kick_session('hls', 'h1')              # 404 : déjà terminée — but atteint
        with self.assertRaises(media_client.MediaError):    # 409 not_a_reader
            media_client.kick_session('webrtc', 'pub')
        with self.assertRaises(KeyError):
            media_client.kick_session('rtsp', 'x')

    def test_kick_and_list_fail_loudly_when_brick_fails(self):
        with self.down():
            with self.assertRaises(media_client.MediaError):
                media_client.list_sessions('hls')
            with self.assertRaises(media_client.MediaError):
                media_client.kick_session('hls', 'h1')
        with self.bad_creds(), self.assertLogs('api.media', level='ERROR'):
            with self.assertRaises(media_client.MediaAuthError):
                media_client.list_sessions('webrtc')
            with self.assertRaises(media_client.MediaAuthError):
                media_client.kick_session('webrtc', 'w1')

    def test_playback_list(self):
        self.assertEqual(media_client.playback_list('webcam/av'), [])        # 200 [] : rien d'enregistré
        ranges = [{'start': '2026-10-11T01:54:43.036558Z', 'duration': 3.001},
                  {'start': '2026-10-11T01:55:44.236485123Z', 'duration': 73.925}]
        self.fake.recordings['webcam/av'] = ranges
        self.assertEqual(media_client.playback_list('webcam/av'), ranges)
        self.assertEqual(self.fake.calls[-1][:3], ('GET', '/v1/recordings', {'path': 'webcam/av'}))
        # 404 unknown_path n'est PLUS « liste vide » : c'est une erreur de registre.
        with self.assertRaises(media_client.MediaError):
            media_client.playback_list('autre/av')
        with self.down(), self.assertRaises(media_client.MediaError):
            media_client.playback_list('webcam/av')
        with self.bad_creds(), self.assertLogs('api.media', level='ERROR'):
            with self.assertRaises(media_client.MediaAuthError):
                media_client.playback_list('webcam/av')

    def test_playback_get(self):
        with self.assertRaises(media_client.MediaError):                     # 404 no_recording
            media_client.playback_get('webcam/av', '2026-10-11T01:55:44Z', 4)
        self.fake.clip = b'\x00\x00\x00\x18ftypiso5' + b'x' * 200_000
        resp = media_client.playback_get('webcam/av', '2026-10-11T01:55:44Z', 4)
        try:
            self.assertEqual(b''.join(resp.iter_content(65536)), self.fake.clip)
        finally:
            resp.close()
        self.assertEqual(self.fake.calls[-1][:3], ('GET', '/v1/recordings/get', {
            'path': 'webcam/av', 'start': '2026-10-11T01:55:44Z', 'duration': '4', 'format': 'fmp4'}))
        with self.down(), self.assertRaises(media_client.MediaError):
            media_client.playback_get('webcam/av', '2026-10-11T01:55:44Z', 4)

    def test_every_call_carries_control_credentials(self):
        media_client.path_ready('webcam/av')
        media_client.list_sessions('hls')
        media_client.kick_session('hls', 'absent')
        media_client.playback_list('webcam/av')
        self.assertEqual(len(self.fake.calls), 4)
        self.assertTrue(all(authed for *_rest, authed in self.fake.calls))


class DevicesTests(FakeBrickCase):
    def test_online_then_offline(self):
        self.grant(video_since=utc(2026, 10, 4, 10))
        self.assertIs(self.client.get('/api/devices/', **self.member).json()[0]['online'], True)
        self.fake.ready['webcam/av'] = False
        self.assertIs(self.client.get('/api/devices/', **self.member).json()[0]['online'], False)

    def test_brick_unreachable_or_refusing_shows_offline_not_an_error(self):
        self.grant(video_since=utc(2026, 10, 4, 10))
        with self.down():
            r = self.client.get('/api/devices/', **self.member)
            self.assertEqual((r.status_code, r.json()[0]['online']), (200, False))
            r = self.client.get('/api/grants/devices/', **self.admin)
            self.assertEqual((r.status_code, r.json()[0]['online']), (200, False))
        with self.bad_creds(), self.assertLogs('api.media', level='ERROR'):
            r = self.client.get('/api/devices/', **self.member)
            self.assertEqual((r.status_code, r.json()[0]['online']), (200, False))


@mock.patch.object(keycloak_admin, 'find_user_by_email', return_value={'id': 'u1'})
class RevocationTests(FakeBrickCase):
    """Révocation ⇒ expulsion, par l'API de contrôle, dans les DEUX protocoles."""

    def open_session(self, sid, protocol, path='webcam/av', token=None):
        """Comme le fait lab-media : relais d'une demande de lecture à notre hook."""
        body = {'ip': '192.168.48.3', 'user': '', 'password': '', 'token': token or make_token(MEMBER),
                'action': 'read', 'path': path, 'protocol': protocol, 'id': sid, 'query': '',
                'userAgent': 'Mozilla/5.0'}
        r = self.client.post(HOOK, data=body, content_type='application/json')
        if r.status_code == 200:
            self.fake.sessions.append(session_item(protocol, sid, path))
        return r.status_code

    def patch(self, grant, body):
        return self.client.patch(f'/api/grants/{grant.id}/', data=body, content_type='application/json',
                                 **self.admin)

    def test_revocation_kicks_hls_and_webrtc(self, _kc):
        g = self.grant(video_since=utc(2026, 10, 4, 10), audio_since=utc(2026, 10, 4, 10))
        self.assertEqual(self.open_session('hls-1', 'hls'), 200)
        self.assertEqual(self.open_session('rtc-1', 'webrtc'), 200)
        self.assertEqual(self.open_session('rtc-audio', 'webrtc', 'webcam/audio'), 200)
        self.assertEqual(self.patch(g, {'video': False}).status_code, 200)
        # av (HLS et WebRTC) coupés ; la session audio seule reste autorisée, donc ouverte.
        self.assertEqual(sorted(self.fake.kicks()),
                         ['/v1/sessions/hls/hls-1/kick', '/v1/sessions/webrtc/rtc-1/kick'])
        self.assertEqual([s['id'] for s in self.fake.sessions], ['rtc-audio'])
        self.assertEqual(set(MediaSession.objects.values_list('id', flat=True)), {'rtc-audio'})
        # … et une nouvelle demande de lecture av est refusée par le hook.
        self.assertEqual(self.open_session('rtc-2', 'webrtc'), 401)

    def test_delete_kicks_everything_of_that_user_only(self, _kc):
        g = self.grant(video_since=utc(2026, 10, 4, 10), audio_since=utc(2026, 10, 4, 10))
        self.grant(ADMIN, video_since=utc(2026, 10, 4, 10), audio_since=utc(2026, 10, 4, 10))
        self.open_session('m-hls', 'hls')
        self.open_session('m-rtc', 'webrtc')
        self.assertEqual(self.open_session('a-rtc', 'webrtc', token=admin_token()), 200)
        r = self.client.delete(f'/api/grants/{g.id}/', **self.admin)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(sorted(self.fake.kicks()),
                         ['/v1/sessions/hls/m-hls/kick', '/v1/sessions/webrtc/m-rtc/kick'])
        self.assertEqual([s['id'] for s in self.fake.sessions], ['a-rtc'])

    def test_revocation_holds_when_brick_unreachable(self, _kc):
        g = self.grant(video_since=utc(2026, 10, 4, 10), audio_since=utc(2026, 10, 4, 10))
        self.open_session('hls-1', 'hls')
        with self.down(), self.assertLogs('api.kick', level='WARNING') as logs:
            self.assertEqual(self.patch(g, {'video': False}).status_code, 200)
        g.refresh_from_db()
        self.assertEqual((g.video, g.video_since), (False, None))        # la révocation est acquise
        self.assertTrue(MediaSession.objects.filter(id='hls-1').exists())  # à couper au retour
        self.assertIn('impossible', logs.output[0])
        self.assertEqual(self.fake.kicks(), [])
        # Personne ne peut ouvrir de nouvelle lecture entre-temps : le hook refuse.
        self.assertEqual(self.open_session('hls-2', 'hls'), 401)

    def test_revocation_holds_when_control_credentials_refused(self, _kc):
        g = self.grant(video_since=utc(2026, 10, 4, 10), audio_since=utc(2026, 10, 4, 10))
        self.open_session('rtc-1', 'webrtc')
        with self.bad_creds(), self.assertLogs('api.kick', level='WARNING'), \
                self.assertLogs('api.media', level='ERROR'):
            self.assertEqual(self.patch(g, {'audio': False}).status_code, 200)
        g.refresh_from_db()
        self.assertFalse(g.audio)
        self.assertEqual(self.fake.kicks(), [])


class RecordingsTests(FakeBrickCase):
    """Relecture par l'API de contrôle : liste et flux, bornés par le `since`."""

    def setUp(self):
        super().setUp()
        # Une plage continue 09:30 → 12:30 UTC le 2026-10-04 (deux segments fusionnés par lab-media).
        self.fake.recordings['webcam/av'] = [{'start': '2026-10-04T09:30:00.123456789Z', 'duration': 10800.0}]

    def list(self, headers):
        return self.client.get('/api/recordings/', {'device': 'webcam', 'date': '2026-10-04'}, **headers)

    def ticket(self, start, duration_s=600):
        return self.client.post('/api/recordings/ticket/', {'device': 'webcam', 'start': start,
                                                            'duration_s': duration_s},
                                content_type='application/json', **self.member)

    def test_list_clipped_at_since(self):
        self.grant(video_since=utc(2026, 10, 4, 11, 20), audio_since=utc(2026, 10, 4, 11, 40))
        r = self.list(self.member)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(p['start'], p['end'], p['tracks']) for p in r.json()], [
            ('2026-10-04T11:20:00Z', '2026-10-04T11:40:00Z', ['video']),
            ('2026-10-04T11:40:00Z', '2026-10-04T12:00:00Z', ['video', 'audio']),
            ('2026-10-04T12:00:00Z', '2026-10-04T12:30:00.123456Z', ['video', 'audio']),
        ])
        self.assertEqual(self.fake.calls[-1][:3], ('GET', '/v1/recordings', {'path': 'webcam/av'}))

    def test_segment_entirely_before_since_is_invisible(self):
        self.grant(video_since=utc(2026, 10, 4, 13), audio_since=utc(2026, 10, 4, 13))
        r = self.list(self.member)
        self.assertEqual((r.status_code, r.json()), (200, []))
        self.assertEqual(self.ticket('2026-10-04T12:00:00Z').status_code, 403)

    def test_ticket_and_stream_inside_and_outside_since(self):
        self.grant(video_since=utc(2026, 10, 4, 11), audio_since=utc(2026, 10, 4, 11))
        self.fake.clip = b'\x00\x00\x00\x18ftypiso5' + b'm' * 50_000
        self.assertEqual(self.ticket('2026-10-04T10:59:59Z').status_code, 403)   # avant le since
        calls = len(self.fake.calls)
        ok = self.ticket('2026-10-04T11:00:00Z')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(len(self.fake.calls), calls)        # un ticket ne coûte aucun appel à lab-media
        resp = self.client.get(ok.json()['url'][len('/peripheriques-api'):])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), self.fake.clip)
        self.assertEqual(self.fake.calls[-1][:3], ('GET', '/v1/recordings/get', {
            'path': 'webcam/av', 'start': '2026-10-04T11:00:00Z', 'duration': '600', 'format': 'fmp4'}))

    def test_stream_refused_after_revocation_without_calling_brick(self):
        g = self.grant(video_since=utc(2026, 10, 4, 11), audio_since=utc(2026, 10, 4, 11))
        self.fake.clip = b'x' * 1000
        ok = self.ticket('2026-10-04T11:00:00Z')
        g.delete()
        calls = len(self.fake.calls)
        self.assertEqual(self.client.get(ok.json()['url'][len('/peripheriques-api'):]).status_code, 403)
        self.assertEqual(len(self.fake.calls), calls)

    def test_brick_unreachable(self):
        self.grant(video_since=utc(2026, 10, 4, 11), audio_since=utc(2026, 10, 4, 11))
        ok = self.ticket('2026-10-04T11:00:00Z')
        with self.down():
            r = self.list(self.member)
            self.assertEqual((r.status_code, r.json()), (503, {'detail': 'Serveur média indisponible.'}))
            self.assertEqual(self.client.get(ok.json()['url'][len('/peripheriques-api'):]).status_code, 502)

    def test_control_credentials_refused(self):
        self.grant(video_since=utc(2026, 10, 4, 11), audio_since=utc(2026, 10, 4, 11))
        self.fake.clip = b'x' * 1000
        ok = self.ticket('2026-10-04T11:00:00Z')
        with self.bad_creds(), self.assertLogs('api.media', level='ERROR'):
            self.assertEqual(self.list(self.member).status_code, 503)
            self.assertEqual(self.client.get(ok.json()['url'][len('/peripheriques-api'):]).status_code, 502)

    def test_no_recording_on_range_502(self):
        self.grant(video_since=utc(2026, 10, 4, 11), audio_since=utc(2026, 10, 4, 11))
        ok = self.ticket('2026-10-04T11:00:00Z')            # fake.clip vide ⇒ 404 no_recording
        self.assertEqual(self.client.get(ok.json()['url'][len('/peripheriques-api'):]).status_code, 502)

    def test_no_grant_never_reaches_brick(self):
        r = self.list(self.member)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.fake.calls, [])
