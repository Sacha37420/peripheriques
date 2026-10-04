"""API REST I3 hors relecture : /me, /devices, /grants (+ kick I4)."""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from api import keycloak_admin
from api.models import Grant, MediaSession

from .helpers import ADMIN, MEMBER, OTHER, admin_token, bearer, make_token, patch_jwks


def fake_live(by_protocol: dict):
    """side_effect de media_client.list_sessions : ids vivants par protocole."""
    return lambda protocol: [{'id': i, 'path': 'webcam/av'} for i in by_protocol.get(protocol, [])]


@patch_jwks()
class AuthTests(TestCase):
    def test_me(self):
        r = self.client.get('/api/me/', **bearer(admin_token()))
        self.assertEqual(r.json(), {'email': ADMIN, 'is_admin': True})
        r = self.client.get('/api/me/', **bearer(make_token()))
        self.assertEqual(r.json(), {'email': MEMBER, 'is_admin': False})

    def test_refusals(self):
        cases = {
            'admin-cli': make_token(ADMIN, groups=('developers',), azp='admin-cli'),
            'sans groupe': make_token(groups=()),
            'groupe hors lab': make_token(groups=('inconnu',)),
            'expiré': make_token(exp_delta=-5),
            'mauvaise signature': make_token(foreign_key=True),
            'sans email': make_token(email=None),
        }
        for name, tok in cases.items():
            for url in ('/api/me/', '/api/devices/', '/api/grants/'):
                r = self.client.get(url, **bearer(tok))
                self.assertIn(r.status_code, (401, 403), f'{name} {url}')
        self.assertIn(self.client.get('/api/me/').status_code, (401, 403))


@patch_jwks()
@mock.patch('api.media_client.path_ready', return_value=True)
class DeviceTests(TestCase):
    def test_admin_without_grant_sees_nothing(self, _ready):
        # v3 : être admin ne donne aucun droit de lecture.
        self.assertEqual(self.client.get('/api/devices/', **bearer(admin_token())).json(), [])

    def test_admin_with_grant_like_anyone(self, _ready):
        since = timezone.now().replace(microsecond=0)
        Grant.objects.create(email=ADMIN, device_id='webcam', audio=True, audio_since=since,
                             granted_by=ADMIN)
        self.assertEqual(self.client.get('/api/devices/', **bearer(admin_token())).json(), [{
            'slug': 'webcam', 'label': 'Webcam (vidéo + micro)', 'online': True,
            'video': {'allowed': False, 'since': None},
            'audio': {'allowed': True, 'since': since.isoformat().replace('+00:00', 'Z')},
        }])

    def test_member_without_grant_sees_nothing(self, _ready):
        self.assertEqual(self.client.get('/api/devices/', **bearer(make_token())).json(), [])

    def test_member_with_video_grant(self, _ready):
        since = timezone.now().replace(microsecond=0) - timedelta(hours=1)
        Grant.objects.create(email=MEMBER, device_id='webcam', video=True, video_since=since,
                             granted_by=ADMIN)
        d = self.client.get('/api/devices/', **bearer(make_token())).json()[0]
        self.assertEqual(d['video'], {'allowed': True,
                                      'since': since.isoformat().replace('+00:00', 'Z')})
        self.assertEqual(d['audio'], {'allowed': False, 'since': None})

    def test_offline_when_media_unreachable(self, ready):
        ready.return_value = False
        Grant.objects.create(email=ADMIN, device_id='webcam', video=True,
                             video_since=timezone.now(), granted_by=ADMIN)
        d = self.client.get('/api/devices/', **bearer(admin_token())).json()[0]
        self.assertFalse(d['online'])


@patch_jwks()
@mock.patch.object(keycloak_admin, 'find_user_by_email', return_value={'id': 'u1'})
class GrantTests(TestCase):
    def post(self, body, tok=None):
        return self.client.post('/api/grants/', data=body, content_type='application/json',
                                **bearer(tok or admin_token()))

    def patch(self, gid, body, tok=None):
        return self.client.patch(f'/api/grants/{gid}/', data=body, content_type='application/json',
                                 **bearer(tok or admin_token()))

    def test_non_admin_403_everywhere(self, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, granted_by=ADMIN)
        tok = make_token(groups=('famille',))
        self.assertEqual(self.client.get('/api/grants/', **bearer(tok)).status_code, 403)
        self.assertEqual(self.post({'email': OTHER, 'device': 'webcam', 'video': True}, tok).status_code, 403)
        self.assertEqual(self.patch(g.id, {'audio': True}, tok).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/grants/{g.id}/', **bearer(tok)).status_code, 403)
        g.refresh_from_db()
        self.assertFalse(g.audio)

    def test_create(self, kc):
        r = self.post({'email': ' Member@SSOLab.local ', 'device': 'webcam', 'video': True})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(body['email'], MEMBER)
        self.assertEqual((body['video'], body['audio']), (True, False))
        self.assertTrue(body['video_since'].endswith('Z'))
        self.assertIsNone(body['audio_since'])
        self.assertEqual(body['granted_by'], ADMIN)
        self.assertEqual(body['device'], 'webcam')
        self.assertEqual(set(body), {'id', 'email', 'device', 'video', 'audio', 'video_since',
                                     'audio_since', 'granted_by', 'updated_at'})
        kc.assert_called_once_with(MEMBER)
        self.assertEqual(len(self.client.get('/api/grants/', **bearer(admin_token())).json()), 1)

    def test_create_unknown_account(self, kc):
        kc.return_value = None
        r = self.post({'email': 'ghost@ssolab.local', 'device': 'webcam', 'audio': True})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'email': ['Aucun compte du lab avec cet email.']})
        self.assertFalse(Grant.objects.exists())

    def test_create_keycloak_down_503(self, kc):
        kc.side_effect = keycloak_admin.KeycloakAdminError('down')
        self.assertEqual(self.post({'email': OTHER, 'device': 'webcam', 'audio': True}).status_code, 503)

    def test_create_requires_a_right(self, _kc):
        r = self.post({'email': OTHER, 'device': 'webcam', 'video': False, 'audio': False})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'non_field_errors': ['Au moins un droit (vidéo ou micro) est requis.']})

    def test_create_duplicate_400(self, _kc):
        self.assertEqual(self.post({'email': OTHER, 'device': 'webcam', 'video': True}).status_code, 201)
        r = self.post({'email': OTHER.upper(), 'device': 'webcam', 'audio': True})
        self.assertEqual(r.status_code, 400)
        self.assertIn('non_field_errors', r.json())

    def test_create_unknown_device_400(self, _kc):
        self.assertEqual(self.post({'email': OTHER, 'device': 'nope', 'video': True}).status_code, 400)

    def test_put_not_allowed(self, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, granted_by=ADMIN)
        r = self.client.put(f'/api/grants/{g.id}/', data={'video': True, 'audio': True},
                            content_type='application/json', **bearer(admin_token()))
        self.assertEqual(r.status_code, 405)

    @mock.patch('api.media_client.kick_session')
    @mock.patch('api.media_client.list_sessions')
    def test_patch_transitions_and_kick(self, live, kick, _kc):
        old = timezone.now().replace(microsecond=0) - timedelta(days=1)
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, video_since=old,
                                 granted_by=ADMIN)
        # true→true conserve since ; false→true pose since
        r = self.patch(g.id, {'video': True, 'audio': True}).json()
        self.assertEqual(r['video_since'], old.isoformat().replace('+00:00', 'Z'))
        self.assertIsNotNone(r['audio_since'])
        kick.assert_not_called()
        live.assert_not_called()

        for sid, path in (('s-v', 'webcam/video'), ('s-a', 'webcam/audio'), ('s-av', 'webcam/av')):
            MediaSession.objects.create(id=sid, email=MEMBER, path=path, protocol='webrtc')
        MediaSession.objects.create(id='s-other', email=OTHER, path='webcam/video', protocol='webrtc')
        live.side_effect = fake_live({'webrtc': ['s-v', 's-a', 's-av', 's-other']})
        # true→false : since=null + kick des sessions qui exigeaient la vidéo
        r = self.patch(g.id, {'video': False}).json()
        self.assertEqual((r['video'], r['video_since']), (False, None))
        self.assertEqual(sorted(c.args for c in kick.call_args_list),
                         [('webrtc', 's-av'), ('webrtc', 's-v')])
        self.assertEqual(set(MediaSession.objects.values_list('id', flat=True)), {'s-a', 's-other'})

    @mock.patch('api.media_client.kick_session')
    @mock.patch('api.media_client.list_sessions')
    def test_revocation_during_hls_session_kicks_hls(self, live, kick, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, audio=True,
                                 granted_by=ADMIN)
        MediaSession.objects.create(id='h1', email=MEMBER, path='webcam/video', protocol='hls')
        MediaSession.objects.create(id='w1', email=MEMBER, path='webcam/av', protocol='webrtc')
        MediaSession.objects.create(id='r1', email=MEMBER, path='webcam/video', protocol='rtsp')
        live.side_effect = fake_live({'hls': ['h1'], 'webrtc': ['w1']})
        self.assertEqual(self.patch(g.id, {'video': False}).status_code, 200)
        self.assertEqual(sorted(c.args for c in kick.call_args_list),
                         [('hls', 'h1'), ('webrtc', 'w1')])
        # rtsp : aucune liste ni kick demandés pour ce protocole
        self.assertEqual(sorted(c.args[0] for c in live.call_args_list), ['hls', 'webrtc'])
        self.assertEqual(set(MediaSession.objects.values_list('id', flat=True)), {'r1'})

    @mock.patch('api.media_client.kick_session')
    @mock.patch('api.media_client.list_sessions')
    def test_other_protocol_never_calls_media(self, live, kick, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, granted_by=ADMIN)
        MediaSession.objects.create(id='r1', email=MEMBER, path='webcam/video', protocol='rtsp')
        MediaSession.objects.create(id='x1', email=MEMBER, path='webcam/video', protocol='')
        self.assertEqual(self.client.delete(f'/api/grants/{g.id}/', **bearer(admin_token())).status_code, 204)
        live.assert_not_called()
        kick.assert_not_called()

    def test_patch_both_false_400(self, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, granted_by=ADMIN)
        self.assertEqual(self.patch(g.id, {'video': False}).status_code, 400)
        self.assertEqual(self.patch(g.id, {'video': False, 'audio': False}).status_code, 400)
        g.refresh_from_db()
        self.assertTrue(g.video)

    def test_patch_cannot_change_email_or_device(self, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, granted_by=ADMIN)
        self.patch(g.id, {'email': OTHER, 'audio': True})
        g.refresh_from_db()
        self.assertEqual(g.email, MEMBER)

    @mock.patch('api.media_client.kick_session')
    @mock.patch('api.media_client.list_sessions')
    def test_delete_kicks_all_but_admin_sessions(self, live, kick, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, audio=True,
                                 granted_by=ADMIN)
        MediaSession.objects.create(id='s1', email=MEMBER, path='webcam/audio', protocol='webrtc')
        MediaSession.objects.create(id='gone', email=MEMBER, path='webcam/av', protocol='hls')
        MediaSession.objects.filter(id='gone').update(created_at=timezone.now() - timedelta(hours=1))
        MediaSession.objects.create(id='fresh', email=MEMBER, path='webcam/av', protocol='webrtc')
        live.side_effect = fake_live({'webrtc': ['s1']})
        r = self.client.delete(f'/api/grants/{g.id}/', **bearer(admin_token()))
        self.assertEqual(r.status_code, 204)
        kick.assert_called_once_with('webrtc', 's1')
        # absente de MediaMTX : purgée si ancienne, gardée si toute récente
        self.assertEqual(set(MediaSession.objects.values_list('id', flat=True)), {'fresh'})

    @mock.patch('api.media_client.kick_session')
    @mock.patch('api.media_client.list_sessions')
    def test_revoking_admin_own_grant_kicks_admin(self, live, kick, _kc):
        # v3 : un admin dont on révoque le Grant est coupé comme les autres.
        g = Grant.objects.create(email=ADMIN, device_id='webcam', video=True, audio=True,
                                 video_since=timezone.now(), audio_since=timezone.now(),
                                 granted_by=ADMIN)
        MediaSession.objects.create(id='a-av', email=ADMIN, path='webcam/av', protocol='webrtc')
        MediaSession.objects.create(id='a-hls', email=ADMIN, path='webcam/audio', protocol='hls')
        live.side_effect = fake_live({'webrtc': ['a-av'], 'hls': ['a-hls']})
        self.assertEqual(self.patch(g.id, {'audio': False}).status_code, 200)
        self.assertEqual(sorted(c.args for c in kick.call_args_list),
                         [('hls', 'a-hls'), ('webrtc', 'a-av')])
        self.assertFalse(MediaSession.objects.exists())

    @mock.patch('api.media_client.list_sessions', side_effect=Exception('media down'))
    def test_revocation_survives_kick_failure(self, _live, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, audio=True,
                                 granted_by=ADMIN)
        MediaSession.objects.create(id='s1', email=MEMBER, path='webcam/av', protocol='hls')
        with self.assertLogs('api.kick', 'WARNING'):
            r = self.patch(g.id, {'audio': False})
        self.assertEqual(r.status_code, 200)
        g.refresh_from_db()
        self.assertFalse(g.audio)
        with self.assertLogs('api.kick', 'WARNING'):
            self.assertEqual(self.client.delete(f'/api/grants/{g.id}/', **bearer(admin_token())).status_code, 204)
        self.assertFalse(Grant.objects.exists())

    @mock.patch('api.media_client.kick_session', side_effect=Exception('boom'))
    @mock.patch('api.media_client.list_sessions', side_effect=lambda p: [{'id': 's1', 'path': 'webcam/av'}])
    def test_kick_call_failure_logged(self, _live, _kick, _kc):
        g = Grant.objects.create(email=MEMBER, device_id='webcam', video=True, audio=True,
                                 granted_by=ADMIN)
        MediaSession.objects.create(id='s1', email=MEMBER, path='webcam/av', protocol='webrtc')
        with self.assertLogs('api.kick', 'WARNING'):
            self.assertEqual(self.patch(g.id, {'audio': False}).status_code, 200)
        self.assertTrue(MediaSession.objects.filter(id='s1').exists())


class MediaClientEndpointTests(TestCase):
    """Les bons endpoints I4 v2 sont appelés, en Basic internes."""

    @mock.patch('api.media_client.requests.post')
    @mock.patch('api.media_client.requests.get')
    def test_endpoints(self, get, post):
        from api import media_client
        get.return_value = mock.Mock(status_code=200, json=lambda: {
            'pageCount': 1, 'items': [{'id': 'a', 'path': 'webcam/av'}]})
        post.return_value = mock.Mock(status_code=200)
        for proto, ep in (('webrtc', 'webrtcsessions'), ('hls', 'hlssessions')):
            self.assertEqual(media_client.list_sessions(proto), [{'id': 'a', 'path': 'webcam/av'}])
            self.assertEqual(get.call_args.args[0], f'http://media-test:9997/v3/{ep}/list')
            self.assertEqual(get.call_args.kwargs['auth'], ('backend', 'int-secret'))
            media_client.kick_session(proto, 'abc')
            self.assertEqual(post.call_args.args[0], f'http://media-test:9997/v3/{ep}/kick/abc')
            self.assertEqual(post.call_args.kwargs['auth'], ('backend', 'int-secret'))
        with self.assertRaises(KeyError):
            media_client.kick_session('rtsp', 'abc')
