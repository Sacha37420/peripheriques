"""Chantier « webcams-modeles » (contrat v1) : coupure globale (I0), pont « caméras d'un
modèle » (I1) et ticket de lecture `pt1.` vérifié par le hook (I2).

Même patron que `test_webcams` : vrais JWT RS256 passés par `validate_token`, faux lab-media
HTTP local (`fake_lab_media.py`) qui rend les formes de `lab-media/API.md`.
"""
from __future__ import annotations

import json
import time
from unittest import mock

from django.core import signing
from django.test import override_settings

from api import bridge, log_filters
from api.access import parse_iso
from api.models import Cutoff, Device, MediaSession

from .fake_lab_media import closed_port_url, device_item, session_item
from .helpers import ADMIN, MEMBER, OTHER, bearer, make_token
from .test_webcams import CAM, MIC_CAM, WebcamCase, utc

BRIDGE_URL = '/api/bridge/model-cameras/'
CUTOFF_URL = '/api/admin/cutoff/'
KEY = 'b' * 64                                   # = settings_test.PERIPHERIQUES_BRIDGE_KEY
MODEL = 'modele@ssolab.local'
VIEWER = 'watcher@dev2.local'


class Case(WebcamCase):
    def setUp(self):
        super().setUp()
        # MIC_CAM : « HD USB Camera », avec micro ; CAM : « HD USB Camera 2 », sans micro
        # (même modèle, même first_seen : numérotés par identifiant).
        self.plug(device_item(CAM), device_item(MIC_CAM, has_audio=True))
        self.admin_list()
        self.fake.calls.clear()

    # ── Pont ──
    def ask(self, body=None, key=KEY, method='post', raw=None, auth=None):
        headers = {}
        if auth is not None:
            headers['HTTP_AUTHORIZATION'] = auth
        elif key is not None:
            headers['HTTP_AUTHORIZATION'] = f'Bridge {key}'
        if method == 'get':
            return self.client.get(BRIDGE_URL, **headers)
        data = raw if raw is not None else json.dumps(
            body if body is not None else {'model_email': MODEL, 'viewer_email': VIEWER})
        return getattr(self.client, method)(BRIDGE_URL, data=data, content_type='application/json',
                                            **headers)

    def ticket(self, model=MODEL):
        r = self.ask({'model_email': model, 'viewer_email': VIEWER})
        self.assertEqual(r.status_code, 200)
        return r.json()['ticket']

    # ── Coupure ──
    def cut(self, active=True, headers=None):
        return self.client.post(CUTOFF_URL, data=json.dumps({'active': active}),
                                content_type='application/json',
                                **(self.admin if headers is None else headers))

    def record_on(self, *slugs):
        for d in self.fake.devices:
            if d['id'] in slugs:
                d['record'] = True
        Device.objects.filter(slug__in=slugs).update(record=True)

    def fake_record(self, slug):
        return next(d for d in self.fake.devices if d['id'] == slug)['record']


# ── I1 — pont ────────────────────────────────────────────────────────────────

class BridgeTests(Case):
    def test_exact_shape_video_only_and_av(self):
        self.grant(CAM, email=MODEL, video=True)                       # pas de micro sur CAM
        self.grant(MIC_CAM, email=MODEL, video=True, audio=True)
        self.grant('webcam-ancienne', email=MODEL, video=True, audio=False)
        before = int(time.time())
        r = self.ask({'model_email': '  Modele@SSOLAB.local ', 'viewer_email': VIEWER.upper()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/json')
        body = r.json()
        self.assertEqual(set(body), {'model_email', 'devices', 'speakers', 'ticket', 'expires_at'})
        self.assertEqual(body['speakers'], [])
        self.assertEqual(body['model_email'], MODEL)
        # Ordre par nom d'affichage ; av seulement avec audio ET micro ; jamais d'audio seul.
        self.assertEqual(body['devices'], [
            {'id': 'webcam-ancienne', 'label': 'Ancienne webcam', 'connected': False,
             'state': 'legacy', 'paths': {'video': 'webcam/video', 'av': None}},
            {'id': MIC_CAM, 'label': 'HD USB Camera', 'connected': True, 'state': 'live',
             'paths': {'video': f'webcam/{MIC_CAM}/video', 'av': f'webcam/{MIC_CAM}/av'}},
            {'id': CAM, 'label': 'HD USB Camera 2', 'connected': True, 'state': 'live',
             'paths': {'video': f'webcam/{CAM}/video', 'av': None}},
        ])
        self.assertTrue(body['ticket'].startswith('pt1.'))
        self.assertRegex(body['expires_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        exp = int(parse_iso(body['expires_at']).timestamp())
        self.assertTrue(before + 119 <= exp <= int(time.time()) + 120)

    def test_audio_only_and_no_right_excluded(self):
        self.grant(MIC_CAM, email=MODEL, video=False, audio=True)      # micro seul : exclu
        self.grant(CAM, email=OTHER, video=True)                       # droit d'un autre
        r = self.ask()
        self.assertEqual(r.json(), {'model_email': MODEL, 'devices': [], 'speakers': [],
                                    'ticket': None, 'expires_at': None})

    def test_video_without_audio_right_on_mic_cam_has_null_av(self):
        self.grant(MIC_CAM, email=MODEL, video=True, audio=False)
        dev = self.ask().json()['devices']
        self.assertEqual(dev[0]['paths'], {'video': f'webcam/{MIC_CAM}/video', 'av': None})

    def test_bad_missing_empty_key_identical_404(self):
        self.grant(CAM, email=MODEL, video=True)
        tok = make_token(ADMIN, groups=('developers',))
        responses = [
            self.ask(key=None),
            self.ask(key=''),
            self.ask(key='a' * 64),
            self.ask(key=KEY[:-1]),
            self.ask(key=KEY + 'b'),
            self.ask(auth=KEY),                                # sans le schéma « Bridge »
            self.ask(auth=f'bridge {KEY}'),
            self.ask(auth=f'Bearer {KEY}'),
            self.ask(auth=f'Bearer {tok}'),                    # jeton Keycloak : refusé
            self.ask(key=None, raw=b'not json'),               # même corps illisible : 404 d'abord
            self.ask(key='x', method='get'),
            self.ask(key=None, method='get'),
            self.ask(key=None, method='delete'),
        ]
        with self.settings(PERIPHERIQUES_BRIDGE_KEY=''):
            responses += [self.ask(), self.ask(key=''), self.ask(auth='Bridge ')]
        for r in responses:
            self.assertEqual(r.status_code, 404)
            self.assertEqual(r.content, b'{"detail": "Not found."}')
            self.assertEqual(r['Content-Type'], 'application/json')
        self.assertEqual({tuple(sorted(r.headers.keys())) for r in responses},
                         {tuple(sorted(responses[0].headers.keys()))})
        self.assertFalse(MediaSession.objects.exists())

    def test_only_post(self):
        for method in ('get', 'put', 'patch', 'delete'):
            r = self.ask(method=method)
            self.assertEqual(r.status_code, 405, method)
            self.assertNotIn('ticket', r.json())

    def test_bad_bodies_400(self):
        self.grant(CAM, email=MODEL, video=True)
        for raw in (b'', b'not json', b'[1]', b'"x"',
                    json.dumps({'model_email': MODEL}).encode(),
                    json.dumps({'viewer_email': VIEWER}).encode(),
                    json.dumps({'model_email': MODEL, 'viewer_email': VIEWER, 'x': 1}).encode(),
                    json.dumps({'model_email': 'pas-un-email', 'viewer_email': VIEWER}).encode(),
                    json.dumps({'model_email': MODEL, 'viewer_email': ''}).encode(),
                    json.dumps({'model_email': MODEL, 'viewer_email': None}).encode(),
                    json.dumps({'model_email': ['a@b.c'], 'viewer_email': VIEWER}).encode()):
            r = self.ask(raw=raw)
            self.assertEqual(r.status_code, 400, raw)
            self.assertEqual(set(r.json()), {'detail'})

    def test_cutoff_has_no_effect_on_bridge(self):
        self.grant(CAM, email=MODEL, video=True)
        before = self.ask().json()
        self.assertEqual(self.cut().status_code, 200)
        after = self.ask().json()
        self.assertEqual(after['devices'], before['devices'])
        self.assertTrue(after['ticket'].startswith('pt1.'))

    def test_logs_never_carry_key_nor_ticket(self):
        self.grant(CAM, email=MODEL, video=True)
        with self.assertLogs('api', level='INFO') as logs:
            ticket = self.ticket()
            self.hook(f'webcam/{CAM}/video', token=ticket, sid='b1')
        text = '\n'.join(logs.output)
        self.assertIn(VIEWER, text)
        self.assertIn(MODEL, text)
        self.assertNotIn(KEY, text)
        self.assertNotIn(ticket[4:], text)
        # Filet du filtre de journal.
        red = log_filters.redact(f'Authorization: Bridge {KEY} token={ticket}')
        self.assertNotIn(KEY, red)
        self.assertNotIn(ticket[4:], red)


# ── I2 — ticket vérifié par le hook ──────────────────────────────────────────

class TicketHookTests(Case):
    def setUp(self):
        super().setUp()
        self.grant(CAM, email=MODEL, video=True)

    def test_ticket_opens_webrtc_session_kept_as_model_bridge(self):
        ticket = self.ticket()
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=ticket, sid='b1'), 200)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token='Bearer ' + ticket, sid='b2'), 200)
        s = MediaSession.objects.get(id='b1')
        self.assertEqual((s.email, s.viewer_email, s.bridge, s.protocol, s.path),
                         (MODEL, VIEWER, True, 'webrtc', f'webcam/{CAM}/video'))

    def test_ticket_reusable_for_several_cams_of_same_model(self):
        self.grant(MIC_CAM, email=MODEL, video=True, audio=True)
        ticket = self.ticket()
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=ticket, sid='b1'), 200)
        self.assertEqual(self.hook(f'webcam/{MIC_CAM}/av', token=ticket, sid='b2'), 200)

    def test_ticket_refusals(self):
        ticket = self.ticket()
        raw = ticket[4:]
        payload = signing.loads(raw, salt=bridge.TICKET_SALT)
        cases = {
            'hls': (f'webcam/{CAM}/video', ticket, 'hls'),
            'rtsp': (f'webcam/{CAM}/video', ticket, 'rtsp'),
            'non accordé (autre webcam)': (f'webcam/{MIC_CAM}/video', ticket, 'webrtc'),
            'av sans micro': (f'webcam/{CAM}/av', ticket, 'webrtc'),
            'audio': (f'webcam/{MIC_CAM}/audio', ticket, 'webrtc'),
            'falsifié': (f'webcam/{CAM}/video', ticket[:-2] + ('AA' if ticket[-2:] != 'AA' else 'BB'),
                         'webrtc'),
            'autre sel': (f'webcam/{CAM}/video',
                          'pt1.' + signing.dumps(payload, salt='peripheriques.recording-ticket'),
                          'webrtc'),
            'charge modifiée': (f'webcam/{CAM}/video', 'pt1.' + ('f' if raw[0] != 'f' else 'g') + raw[1:],
                                'webrtc'),
            'sans préfixe': (f'webcam/{CAM}/video', raw, 'webrtc'),
            'préfixe seul': (f'webcam/{CAM}/video', 'pt1.', 'webrtc'),
            'exp forgé non signé': (f'webcam/{CAM}/video', 'pt1.' + json.dumps(payload), 'webrtc'),
        }
        for what, (path, tok, protocol) in cases.items():
            self.assertEqual(self.hook(path, token=tok, sid='x', protocol=protocol), 401, what)
        self.assertFalse(MediaSession.objects.exists())

    def test_ticket_of_model_without_right_refused(self):
        # Un ticket signé pour un email qui n'a aucun droit (forgé avec la vraie clé).
        tok, _ = bridge.issue_ticket(OTHER, VIEWER)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=tok, sid='x'), 401)

    def test_expired_ticket_refused(self):
        ticket = self.ticket()
        later = time.time() + bridge.TICKET_TTL_S + 2
        with mock.patch('time.time', return_value=later):
            self.assertEqual(self.hook(f'webcam/{CAM}/video', token=ticket, sid='x'), 401)
        # Juste avant l'échéance : encore valable.
        with mock.patch('time.time', return_value=time.time() + bridge.TICKET_TTL_S - 2):
            self.assertEqual(self.hook(f'webcam/{CAM}/video', token=ticket, sid='y'), 200)

    def test_signed_payload_with_past_exp_refused(self):
        tok = 'pt1.' + signing.dumps({'m': MODEL, 'v': VIEWER, 'exp': int(time.time()) - 1},
                                     salt=bridge.TICKET_SALT)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=tok, sid='x'), 401)

    def test_ticket_without_session_id_refused(self):
        body = {'ip': '1.2.3.4', 'user': '', 'password': '', 'token': self.ticket(),
                'action': 'read', 'path': f'webcam/{CAM}/video', 'protocol': 'webrtc',
                'query': '', 'userAgent': 'x'}
        r = self.client.post('/api/media-auth/?key=hook-key-for-tests', data=body,
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_right_checked_at_hook_time(self):
        ticket = self.ticket()
        Device.objects.get(slug=CAM).grants.update(video=False, video_since=None)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=ticket, sid='x'), 401)

    def test_ticket_never_validates_as_jwt_on_rest_api(self):
        ticket = self.ticket()
        self.assertIn(self.client.get('/api/devices/', **bearer(ticket)).status_code, (401, 403))

    @mock.patch('api.keycloak_admin.find_user_by_email', return_value={'id': 'u'})
    def test_revoking_model_right_kicks_bridge_session(self, _kc):
        ticket = self.ticket()
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=ticket, sid='b1'), 200)
        g = Device.objects.get(slug=CAM).grants.get(email=MODEL)
        r = self.client.delete(f'/api/grants/{g.id}/', **self.admin)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.fake.kicks(), ['/v1/sessions/webrtc/b1/kick'])
        self.assertFalse(MediaSession.objects.exists())


# ── I0 — coupure ─────────────────────────────────────────────────────────────

class CutoffTests(Case):
    def setUp(self):
        super().setUp()
        self.grant(CAM, email=MEMBER, video=True)
        self.grant(CAM, email=MODEL, video=True)

    def test_admin_only(self):
        cli = bearer(make_token(ADMIN, groups=('developers',), azp='admin-cli'))
        for headers in (self.member, cli, {}):
            self.assertIn(self.client.get(CUTOFF_URL, **headers).status_code, (401, 403))
            self.assertIn(self.cut(headers=headers).status_code, (401, 403))
        self.assertFalse(Cutoff.is_active())
        self.assertEqual(self.fake.kicks(), [])

    def test_bad_bodies(self):
        for body in ({}, {'active': 'true'}, {'active': 1}, {'active': True, 'x': 1}, ['active']):
            r = self.client.post(CUTOFF_URL, data=json.dumps(body), content_type='application/json',
                                 **self.admin)
            self.assertEqual(r.status_code, 400, body)
        self.assertFalse(Cutoff.is_active())

    def test_initial_state(self):
        r = self.client.get(CUTOFF_URL, **self.admin)
        self.assertEqual(r.json(), {'active': False, 'since': None, 'by': None, 'paused': [],
                                    'failures': [], 'media_errors': []})

    def test_jwt_refused_ticket_accepted_during_cutoff(self):
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='before'), 200)
        r = self.cut()
        self.assertEqual(r.status_code, 200)
        state = r.json()
        self.assertTrue(state['active'])
        self.assertEqual(state['by'], ADMIN)
        self.assertRegex(state['since'], r'Z$')
        # Persistant : relu en base.
        self.assertEqual(self.client.get(CUTOFF_URL, **self.admin).json()['since'], state['since'])
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='j1'), 401)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='j2', protocol='hls'), 401)
        self.assertFalse(MediaSession.objects.filter(id__in=('j1', 'j2')).exists())
        ticket = self.ticket()
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=ticket, sid='t1'), 200)
        # Levée : le JWT repasse.
        self.assertFalse(self.cut(False).json()['active'])
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='j3'), 200)

    def test_activation_kicks_jwt_sessions_only(self):
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='rtc-jwt'), 200)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='hls-jwt', protocol='hls'), 200)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', token=self.ticket(), sid='rtc-pont'), 200)
        # Lecture par JWT que le hook n'a pas pu garder (absente de MediaSession) : expulsée aussi.
        self.fake.sessions.append(session_item('webrtc', 'rtc-inconnue', f'webcam/{CAM}/video'))
        # Publication : jamais touchée.
        self.fake.sessions.append(session_item('rtsp', 'pub', f'webcam/{CAM}/av', state='publish'))
        self.fake.sessions.append(session_item('webrtc', 'pub-rtc', f'webcam/{CAM}/av',
                                               state='publish'))
        r = self.cut()
        self.assertEqual(r.json()['media_errors'], [])
        self.assertEqual(sorted(self.fake.kicks()), [
            '/v1/sessions/hls/hls-jwt/kick',
            '/v1/sessions/webrtc/rtc-inconnue/kick',
            '/v1/sessions/webrtc/rtc-jwt/kick',
        ])
        self.assertEqual(list(MediaSession.objects.values_list('id', flat=True)), ['rtc-pont'])
        self.assertIn('rtc-pont', {s['id'] for s in self.fake.sessions})

    def test_record_stopped_then_restored_exactly(self):
        self.record_on(CAM)                       # MIC_CAM reste coupée, l'héritée aussi
        state = self.cut().json()
        self.assertEqual(state['paused'], [{'id': CAM, 'label': 'HD USB Camera 2'}])
        self.assertEqual(state['failures'], [])
        self.assertFalse(self.fake_record(CAM))
        self.assertFalse(Device.objects.get(slug=CAM).record)
        self.assertIn({'record': False}, self.fake.bodies)
        # Activer deux fois ne change pas la liste à rétablir (CAM déjà coupée).
        self.assertEqual(self.cut().json()['paused'], [{'id': CAM, 'label': 'HD USB Camera 2'}])
        self.fake.bodies.clear()
        state = self.cut(False).json()
        self.assertEqual(state, {'active': False, 'since': None, 'by': None, 'paused': [],
                                 'failures': [], 'media_errors': []})
        self.assertTrue(self.fake_record(CAM))
        self.assertFalse(self.fake_record(MIC_CAM))             # jamais activée : reste coupée
        self.assertEqual(self.fake.bodies, [{'record': True}])  # une seule webcam rétablie
        self.assertTrue(Device.objects.get(slug=CAM).record)
        self.assertFalse(Device.objects.get(slug=MIC_CAM).record)

    def test_patch_record_true_409_during_cutoff(self):
        self.cut()
        self.fake.bodies.clear()
        r = self.patch_device(CAM, {'record': True})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(set(r.json()), {'record'})
        self.assertEqual(self.fake.bodies, [])               # rien relayé à lab-media
        self.assertFalse(self.fake_record(CAM))
        # Le reste de la page Périphériques fonctionne : renommer, couper.
        self.assertEqual(self.patch_device(CAM, {'label': 'Salon'}).status_code, 200)
        self.assertEqual(self.patch_device(CAM, {'record': False}).status_code, 200)
        self.cut(False)
        self.assertEqual(self.patch_device(CAM, {'record': True}).status_code, 200)

    def test_manual_record_false_during_cutoff_not_restored(self):
        self.record_on(CAM, MIC_CAM)
        self.assertEqual(len(self.cut().json()['paused']), 2)
        self.assertEqual(self.patch_device(MIC_CAM, {'record': False}).status_code, 200)
        self.assertEqual([p['id'] for p in self.client.get(CUTOFF_URL, **self.admin).json()['paused']],
                         [CAM])
        self.cut(False)
        self.assertTrue(self.fake_record(CAM))
        self.assertFalse(self.fake_record(MIC_CAM))

    def test_forgotten_webcam_ignored_on_restore(self):
        self.record_on(CAM, MIC_CAM)
        self.cut()
        # MIC_CAM débranchée puis oubliée pendant la coupure.
        next(d for d in self.fake.devices if d['id'] == MIC_CAM)['connected'] = False
        Device.objects.filter(slug=MIC_CAM).update(connected=False)
        self.assertEqual(self.client.delete(f'/api/admin/devices/{MIC_CAM}/', **self.admin).status_code,
                         204)
        self.fake.bodies.clear()
        state = self.cut(False).json()
        self.assertEqual((state['paused'], state['failures']), ([], []))
        self.assertEqual(self.fake.bodies, [{'record': True}])
        self.assertTrue(self.fake_record(CAM))

    def test_forgotten_by_lab_media_only_ignored(self):
        self.record_on(CAM)
        self.cut()
        self.fake.devices = [d for d in self.fake.devices if d['id'] != CAM]   # 404 côté lab-media
        state = self.cut(False).json()
        self.assertEqual((state['active'], state['paused'], state['failures']), (False, [], []))

    def test_lab_media_down_cutoff_acquired_and_replayable(self):
        self.record_on(CAM)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='rtc-jwt'), 200)
        with override_settings(MEDIA_API_URL=closed_port_url()), self.assertLogs('api', 'WARNING'):
            state = self.cut().json()
        # Acquise en base malgré tout : le hook refuse le JWT.
        self.assertTrue(state['active'])
        self.assertEqual(set(state['media_errors']), {'sessions', 'inventory'})
        self.assertEqual(state['failures'], [{'id': CAM, 'label': 'HD USB Camera 2', 'action': 'stop'}])
        self.assertEqual(state['paused'], [{'id': CAM, 'label': 'HD USB Camera 2'}])
        self.assertTrue(self.fake_record(CAM))              # pas coupé
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='j2'), 401)
        # Rejouer quand lab-media revient : tout est fait, les erreurs disparaissent.
        state = self.cut().json()
        self.assertEqual((state['media_errors'], state['failures']), ([], []))
        self.assertFalse(self.fake_record(CAM))
        self.assertEqual(self.fake.kicks(), ['/v1/sessions/webrtc/rtc-jwt/kick'])
        self.assertEqual(state['since'], self.client.get(CUTOFF_URL, **self.admin).json()['since'])

    def test_restore_failure_lifts_cutoff_and_is_replayable(self):
        self.record_on(CAM)
        self.cut()
        with override_settings(MEDIA_API_URL=closed_port_url()), self.assertLogs('api', 'WARNING'):
            state = self.cut(False).json()
        self.assertFalse(state['active'])
        self.assertEqual(state['failures'], [{'id': CAM, 'label': 'HD USB Camera 2', 'action': 'restore'}])
        self.assertEqual(state['paused'], [{'id': CAM, 'label': 'HD USB Camera 2'}])
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='j'), 200)   # le direct est rendu
        state = self.cut(False).json()
        self.assertEqual((state['paused'], state['failures']), ([], []))
        self.assertTrue(self.fake_record(CAM))

    def test_replay_does_not_move_since(self):
        first = self.cut().json()['since']
        with mock.patch('api.cutoff.now_s', return_value=utc(2030, 1, 1)):
            self.assertEqual(self.cut().json()['since'], first)

    def test_playback_unchanged_by_cutoff(self):
        self.cut()
        self.fake.recordings[f'webcam/{CAM}/av'] = [{'start': '2026-10-17T07:30:00Z', 'duration': 3600.0}]
        r = self.client.get('/api/recordings/', {'device': CAM, 'date': '2026-10-17'}, **self.member)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(s['start'], s['tracks']) for s in r.json()],
                         [('2026-10-17T08:00:00Z', ['video'])])
        self.fake.clip = b'\x00\x00\x00\x18ftypiso5' + b'v' * 1000
        t = self.client.post('/api/recordings/ticket/', {'device': CAM, 'start': '2026-10-17T08:00:00Z',
                                                         'duration_s': 60},
                             content_type='application/json', **self.member)
        self.assertEqual(t.status_code, 200)
        with mock.patch('api.recordings._Remux.__iter__', return_value=iter([b'ok'])):
            s = self.client.get(t.json()['url'][len('/peripheriques-api'):])
            self.assertEqual(s.status_code, 200)
            b''.join(s.streaming_content)

    def test_member_device_list_unchanged_by_cutoff(self):
        before = self.client.get('/api/devices/', **self.member).json()
        self.cut()
        self.assertEqual(self.client.get('/api/devices/', **self.member).json(), before)
