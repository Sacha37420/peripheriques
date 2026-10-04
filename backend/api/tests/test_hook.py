"""Hook d'autorisation MediaMTX (I2) — règles 1 à 5, cas négatifs compris."""
import json
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from api.models import Grant, MediaSession

from .helpers import ADMIN, MEMBER, OTHER, admin_token, make_token, patch_jwks

KEY = 'hook-key-for-tests'
URL = '/api/media-auth/'


@patch_jwks()
class HookTests(TestCase):
    def call(self, body, key=KEY, method='post'):
        url = URL if key is None else f'{URL}?key={key}'
        payload = {'user': '', 'password': '', 'token': '', 'ip': '203.0.113.9',
                   'action': 'read', 'path': 'webcam/av', 'protocol': 'webrtc',
                   'id': 'sess-1', 'query': ''}
        payload.update(body)
        return getattr(self.client, method)(url, data=json.dumps(payload),
                                            content_type='application/json')

    def grant(self, email=MEMBER, video=True, audio=False):
        now = timezone.now()
        return Grant.objects.create(email=email, device_id='webcam', video=video, audio=audio,
                                    video_since=now if video else None,
                                    audio_since=now if audio else None,
                                    granted_by=ADMIN)

    # ── Règle 1 : clé ─────────────────────────────────────────────────────────
    def test_key_missing_wrong_or_get_all_401_with_empty_body(self):
        # Grant présent : seul le contrôle testé ici peut expliquer le refus.
        self.grant(ADMIN, video=True, audio=True)
        tok = admin_token()
        for key in (None, '', 'wrong', KEY + 'x', KEY[:-1]):
            r = self.call({'token': tok}, key=key)
            self.assertEqual(r.status_code, 401, key)
            self.assertEqual(r.content, b'')
        r = self.client.get(URL)
        self.assertEqual((r.status_code, r.content), (401, b''))
        r = self.client.get(f'{URL}?key={KEY}')
        self.assertEqual((r.status_code, r.content), (401, b''))

    def test_empty_configured_key_refuses_everything(self):
        # Grant présent : seul le contrôle testé ici peut expliquer le refus.
        self.grant(ADMIN, video=True, audio=True)
        with self.settings(MEDIA_HOOK_KEY=''):
            self.assertEqual(self.call({'token': admin_token()}, key='').status_code, 401)

    def test_garbage_body_401(self):
        r = self.client.post(f'{URL}?key={KEY}', data=b'not json', content_type='application/json')
        self.assertEqual(r.status_code, 401)
        r = self.client.post(f'{URL}?key={KEY}', data=b'[1]', content_type='application/json')
        self.assertEqual(r.status_code, 401)

    # ── Règle 2 : publish ─────────────────────────────────────────────────────
    def test_publish(self):
        ok = {'action': 'publish', 'user': 'publisher', 'password': 'pub-secret'}
        for kind in ('video', 'audio', 'av'):
            self.assertEqual(self.call({**ok, 'path': f'webcam/{kind}'}).status_code, 200)
        self.assertEqual(self.call({**ok, 'password': 'nope'}).status_code, 401)
        self.assertEqual(self.call({**ok, 'user': 'backend', 'password': 'int-secret'}).status_code, 401)
        self.assertEqual(self.call({**ok, 'path': 'webcam/other'}).status_code, 401)
        self.assertEqual(self.call({**ok, 'path': 'unknown/av'}).status_code, 401)
        self.assertEqual(self.call({**ok, 'path': 'webcam/av/x'}).status_code, 401)
        # un JWT, même admin, ne permet pas de publier
        self.assertEqual(self.call({'action': 'publish', 'token': admin_token()}).status_code, 401)

    # ── Règle 3 : actions internes ────────────────────────────────────────────
    def test_internal_actions(self):
        for action in ('playback', 'api', 'metrics', 'pprof'):
            ok = self.call({'action': action, 'user': 'backend', 'password': 'int-secret', 'path': ''})
            self.assertEqual(ok.status_code, 200, action)
            bad = self.call({'action': action, 'user': 'backend', 'password': 'x'})
            self.assertEqual(bad.status_code, 401, action)
            pub = self.call({'action': action, 'user': 'publisher', 'password': 'pub-secret'})
            self.assertEqual(pub.status_code, 401, action)
            # action `api` avec un JWT utilisateur (même admin) → refus
            jwt_user = self.call({'action': action, 'token': admin_token()})
            self.assertEqual(jwt_user.status_code, 401, action)

    def test_internal_creds_empty_config_never_match(self):
        with self.settings(MEDIA_INTERNAL_USER='', MEDIA_INTERNAL_PASSWORD=''):
            r = self.call({'action': 'api', 'user': '', 'password': ''})
            self.assertEqual(r.status_code, 401)

    # ── Règle 4 : read ────────────────────────────────────────────────────────
    def test_read_admin_without_grant_refused(self):
        # v3 : être admin ne donne aucun droit de lecture.
        for kind in ('video', 'audio', 'av'):
            r = self.call({'token': admin_token(), 'path': f'webcam/{kind}', 'id': f's-{kind}'})
            self.assertEqual(r.status_code, 401, kind)
        self.assertFalse(MediaSession.objects.exists())

    def test_read_admin_with_grant_like_anyone(self):
        self.grant(ADMIN, video=True, audio=False)
        self.assertEqual(self.call({'token': admin_token(), 'path': 'webcam/video', 'id': 's1'}).status_code, 200)
        self.assertEqual(self.call({'token': admin_token(), 'path': 'webcam/av', 'id': 's2'}).status_code, 401)
        s = MediaSession.objects.get(id='s1')
        self.assertEqual((s.email, s.path, s.protocol), (ADMIN, 'webcam/video', 'webrtc'))

    def test_read_admin_cli_token_refused(self):
        # Grant présent : seul le contrôle testé ici peut expliquer le refus.
        self.grant(ADMIN, video=True, audio=True)
        tok = make_token(ADMIN, groups=('developers',), azp='admin-cli')
        self.assertEqual(self.call({'token': tok}).status_code, 401)

    def test_read_non_member_of_any_group_refused(self):
        self.grant(OTHER, video=True, audio=True)
        tok = make_token(OTHER, groups=())
        self.assertEqual(self.call({'token': tok, 'path': 'webcam/video'}).status_code, 401)

    def test_read_member_without_grant_refused(self):
        self.assertEqual(self.call({'token': make_token(), 'path': 'webcam/video'}).status_code, 401)
        self.assertFalse(MediaSession.objects.exists())

    def test_read_audio_grant(self):
        self.grant(video=False, audio=True)
        tok = make_token()
        self.assertEqual(self.call({'token': tok, 'path': 'webcam/audio'}).status_code, 200)
        self.assertEqual(self.call({'token': tok, 'path': 'webcam/av'}).status_code, 401)
        self.assertEqual(self.call({'token': tok, 'path': 'webcam/video'}).status_code, 401)

    def test_read_av_needs_both(self):
        self.grant(video=True, audio=True)
        self.assertEqual(self.call({'token': make_token(), 'path': 'webcam/av'}).status_code, 200)

    def test_read_email_case_insensitive(self):
        self.grant(video=True)
        tok = make_token(MEMBER.upper())
        self.assertEqual(self.call({'token': tok, 'path': 'webcam/video'}).status_code, 200)

    def test_read_bad_tokens(self):
        self.grant(video=True, audio=True)
        for tok in (make_token(exp_delta=-10), make_token(foreign_key=True),
                    make_token(email=None), 'garbage', ''):
            self.assertEqual(self.call({'token': tok, 'path': 'webcam/av'}).status_code, 401)
        self.assertEqual(self.call({'token': None}).status_code, 401)

    def test_read_bearer_prefix_tolerated(self):
        self.grant(ADMIN, video=True, audio=True)
        self.assertEqual(self.call({'token': 'Bearer ' + admin_token()}).status_code, 200)

    def test_read_unknown_path_refused(self):
        self.grant(ADMIN, video=True, audio=True)
        for path in ('other/av', 'webcam', 'webcam/raw', ''):
            self.assertEqual(self.call({'token': admin_token(), 'path': path}).status_code, 401, path)

    def test_required_groups_empty_fails_closed(self):
        # Grant présent : seul le contrôle testé ici peut expliquer le refus.
        self.grant(ADMIN, video=True, audio=True)
        with self.settings(KEYCLOAK_REQUIRED_GROUPS=''):
            self.assertEqual(self.call({'token': admin_token()}).status_code, 401)

    def test_read_without_id_not_recorded(self):
        self.grant(ADMIN, video=True, audio=True)
        self.assertEqual(self.call({'token': admin_token(), 'id': None, 'protocol': 'hls'}).status_code, 200)
        self.assertFalse(MediaSession.objects.exists())

    def test_hls_session_recorded_with_protocol(self):
        # v2 : le hook n'est appelé qu'à l'ouverture d'une session HLS → il faut la garder pour le kick.
        Grant.objects.create(email=MEMBER, device_id='webcam', video=True,
                             video_since=timezone.now(), granted_by=ADMIN)
        r = self.call({'token': make_token(), 'path': 'webcam/video', 'id': 'hls-1', 'protocol': 'hls'})
        self.assertEqual(r.status_code, 200)
        s = MediaSession.objects.get(id='hls-1')
        self.assertEqual((s.email, s.path, s.protocol), (MEMBER, 'webcam/video', 'hls'))

    def test_old_sessions_purged(self):
        self.grant(ADMIN, video=True, audio=True)
        MediaSession.objects.create(id='old', email=MEMBER, path='webcam/av')
        MediaSession.objects.filter(id='old').update(last_seen=timezone.now() - timedelta(days=31))
        self.call({'token': admin_token(), 'id': 'new'})
        self.assertEqual(set(MediaSession.objects.values_list('id', flat=True)), {'new'})

    # ── Règle 5 ───────────────────────────────────────────────────────────────
    def test_other_actions_refused(self):
        for action in ('', 'write', None, 'READ'):
            r = self.call({'action': action, 'token': admin_token(),
                           'user': 'backend', 'password': 'int-secret'})
            self.assertEqual(r.status_code, 401, action)

    def test_key_compared_in_constant_time(self):
        with mock.patch('api.hook.hmac.compare_digest', wraps=__import__('hmac').compare_digest) as cd:
            self.call({'token': admin_token()}, key='wrong')
        self.assertTrue(cd.called)
