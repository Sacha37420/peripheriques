"""Chantier « haut-parleur » (contrat v1) : haut-parleur de l'inventaire (I1), droit « Parler »,
hook `publish` par jeton Keycloak ou par ticket du pont (I2, I5), expulsion des publieurs (I3) à
la coupure et à la révocation, pont augmenté de `speakers` (I4).

Même patron que `test_webcams_modeles` : vrais JWT RS256 passés par `validate_token`, faux
lab-media HTTP local qui rend les formes de `lab-media/API.md` (I1 et I3 du contrat compris).
Surtout des cas négatifs : tout ce qui n'est pas exactement autorisé doit être refusé.
"""
from __future__ import annotations

import json
import time
from unittest import mock

from django.core import signing

from api import bridge, inventory, keycloak_admin
from api.models import Device, Grant, MediaSession

from .fake_lab_media import device_item, session_item, speaker_item
from .helpers import ADMIN, MEMBER, OTHER, bearer, make_token
from .test_webcams import CAM, HOOK, MIC_CAM, utc
from .test_webcams_modeles import MODEL, VIEWER, Case

HP = 'hp-jack'
TALK = 'speaker/hp-jack'
HP2 = 'hp-salon'
TALK2 = 'speaker/hp-salon'


class SpeakerCase(Case):
    def setUp(self):
        super().setUp()
        self.plug(speaker_item(HP))
        inventory.invalidate()
        self.admin_list()
        self.fake.calls.clear()

    def speak_grant(self, slug=HP, email=MEMBER, since=None):
        return Grant.objects.create(email=email, device_id=slug, speaker=True,
                                    speaker_since=since or utc(2026, 11, 5, 8), granted_by=ADMIN)

    def publish(self, path=TALK, token=None, sid='p1', protocol='webrtc', action='publish'):
        if token is None:
            token = make_token(MEMBER)
        body = {'ip': '192.168.48.3', 'user': '', 'password': '',
                'token': token, 'action': action, 'path': path,
                'protocol': protocol, 'id': sid, 'query': '', 'userAgent': 'Mozilla/5.0'}
        r = self.client.post(HOOK, data=body, content_type='application/json')
        if r.status_code == 200:
            self.fake.sessions.append(session_item(protocol, sid, path,
                                                   state='publish' if action == 'publish' else 'read'))
        return r.status_code


# ── I1 — inventaire ──────────────────────────────────────────────────────────

class SpeakerInventoryTests(SpeakerCase):
    def test_speaker_synced_from_i1(self):
        d = Device.objects.get(slug=HP)
        self.assertEqual((d.kind, d.label, d.model, d.path_talk, d.record, d.state, d.connected,
                          d.has_audio), ('speaker', 'Haut-parleur (prise jack)',
                                         'Haut-parleur (prise jack)', TALK, False, 'idle', True,
                                         False))
        self.assertEqual(d.paths(), {'av': None, 'video': None, 'audio': None, 'talk': TALK})
        self.assertEqual(Device.by_path(TALK), (d, 'talk'))
        # Webcams : chemin talk vide.
        self.assertEqual(Device.objects.get(slug=CAM).paths()['talk'], None)

    def test_visible_in_grant_devices_and_admin_list(self):
        r = self.client.get('/api/grants/devices/', **self.admin)
        hp = next(x for x in r.json() if x['slug'] == HP)
        self.assertEqual(hp, {'slug': HP, 'kind': 'speaker', 'label': 'Haut-parleur (prise jack)',
                              'online': True, 'has_audio': False, 'state': 'idle',
                              'connected': True})
        item = next(x for x in self.admin_list().json()['items'] if x['id'] == HP)
        self.assertEqual((item['kind'], item['paths'], item['record']),
                         ('speaker', {'av': None, 'video': None, 'audio': None, 'talk': TALK},
                          False))
        # Aucun appel d'état de chemin pour un haut-parleur (rien n'y est publié).
        self.assertFalse([c for c in self.fake.calls if c[1].startswith('/v1/paths/speaker')])

    def test_malformed_speakers_ignored(self):
        bad = []
        for dev_id in ('usb-32e4-9230-p9', 'webcam-hp', 'Hp-jack', 'h', '-hp', 'hp_jack'):
            item = speaker_item('hp-x')
            item['id'] = dev_id
            bad.append(item)
        with_av = speaker_item('hp-av')
        with_av['paths']['av'] = 'speaker/hp-av/av'
        no_talk = speaker_item('hp-none')
        no_talk['paths']['talk'] = None
        elsewhere = speaker_item('hp-elsewhere')
        elsewhere['paths']['talk'] = 'webcam/hp-elsewhere/talk'
        dots = speaker_item('hp-dots')
        dots['paths']['talk'] = 'speaker/../webcam/av'
        stolen = speaker_item('hp-thief')
        stolen['paths']['talk'] = TALK                                # chemin de hp-jack
        webcam_talk = device_item('usb-32e4-9230-p3-9')
        webcam_talk['paths']['talk'] = 'speaker/usb-32e4-9230-p3-9'
        odd_kind = speaker_item('hp-odd')
        odd_kind['kind'] = 'microphone'
        self.plug(*bad, with_av, no_talk, elsewhere, dots, stolen, webcam_talk, odd_kind)
        inventory.invalidate()
        self.admin_list()
        self.assertEqual(set(Device.objects.values_list('slug', flat=True)),
                         {'webcam-ancienne', CAM, MIC_CAM, HP})

    def test_speaker_record_patch_refused_409(self):
        r = self.patch_device(HP, {'record': True})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(Device.objects.get(slug=HP).record)
        self.assertFalse([c for c in self.device_calls() if c[0] == 'PATCH'])
        # Le renommage, lui, reste permis.
        self.assertEqual(self.patch_device(HP, {'label': 'Haut-parleur'}).status_code, 200)

    def test_offline_and_error_states(self):
        self.fake.devices[-1].update(state='offline', connected=False)
        inventory.invalidate()
        self.admin_list()
        d = Device.objects.get(slug=HP)
        self.assertEqual((d.state, d.connected), ('offline', False))
        self.speak_grant()
        item = self.client.get('/api/devices/', **self.member).json()[0]
        self.assertFalse(item['online'])
        self.fake.devices[-1].update(state='error', connected=True, error_detail='ALSA en échec')
        inventory.invalidate()
        self.admin_list()
        self.assertEqual(Device.objects.get(slug=HP).error_detail, 'ALSA en échec')
        self.assertFalse(self.client.get('/api/devices/', **self.member).json()[0]['online'])

    def test_member_device_list(self):
        self.speak_grant()
        items = self.client.get('/api/devices/', **self.member).json()
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual((it['slug'], it['kind'], it['online'], it['state'], it['paths']['talk']),
                         (HP, 'speaker', True, 'idle', TALK))
        self.assertEqual(it['speaker'], {'allowed': True, 'since': '2026-11-05T08:00:00Z'})
        self.assertEqual(it['video'], {'allowed': False, 'since': None})
        # Sans droit : invisible.
        self.assertEqual(self.client.get('/api/devices/', **bearer(make_token(OTHER))).json(), [])

    def test_no_recording_through_speaker_right(self):
        self.speak_grant()
        r = self.client.get('/api/recordings/', {'device': HP, 'date': '2026-11-05'}, **self.member)
        self.assertEqual(r.status_code, 403)
        r = self.client.post('/api/recordings/ticket/', data=json.dumps(
            {'device': HP, 'start': '2026-11-05T09:00:00Z', 'duration_s': 60}),
            content_type='application/json', **self.member)
        self.assertEqual(r.status_code, 403)
        self.assertFalse([c for c in self.fake.calls if c[1].startswith('/v1/recordings')])


# ── Droits « Parler » ────────────────────────────────────────────────────────

@mock.patch.object(keycloak_admin, 'find_user_by_email', new=lambda email: {'id': 'u1'})
class SpeakerGrantTests(SpeakerCase):
    def post(self, body):
        return self.client.post('/api/grants/', data=json.dumps(body),
                                content_type='application/json', **self.admin)

    def patch(self, gid, body):
        return self.client.patch(f'/api/grants/{gid}/', data=json.dumps(body),
                                 content_type='application/json', **self.admin)

    def test_create_speaker_grant(self):
        r = self.post({'email': OTHER, 'device': HP, 'speaker': True})
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual((body['speaker'], body['video'], body['audio']), (True, False, False))
        self.assertTrue(body['speaker_since'].endswith('Z'))
        self.assertIsNone(body['video_since'])

    def test_cross_capabilities_400(self):
        cases = [
            ({'email': OTHER, 'device': HP, 'speaker': True, 'video': True}, 'video'),
            ({'email': OTHER, 'device': HP, 'speaker': True, 'audio': True}, 'audio'),
            ({'email': OTHER, 'device': HP, 'video': True}, 'video'),
            ({'email': OTHER, 'device': HP}, 'non_field_errors'),
            ({'email': OTHER, 'device': HP, 'speaker': False}, 'non_field_errors'),
            ({'email': OTHER, 'device': CAM, 'speaker': True}, 'speaker'),
            ({'email': OTHER, 'device': CAM, 'video': True, 'speaker': True}, 'speaker'),
            ({'email': OTHER, 'device': 'webcam-ancienne', 'audio': True, 'speaker': True},
             'speaker'),
        ]
        for body, field in cases:
            r = self.post(body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(field, r.json(), body)
        self.assertFalse(Grant.objects.exists())

    def test_patch_cross_capabilities_400(self):
        hp = self.speak_grant(email=OTHER)
        cam = self.grant(CAM, email=OTHER, video=True)
        for gid, body, field in (
                (hp.id, {'video': True}, 'video'),
                (hp.id, {'audio': True}, 'audio'),
                (hp.id, {'speaker': False}, 'non_field_errors'),
                (cam.id, {'speaker': True}, 'speaker')):
            r = self.patch(gid, body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(field, r.json(), body)
        hp.refresh_from_db()
        cam.refresh_from_db()
        self.assertEqual((hp.speaker, hp.video, hp.audio, cam.speaker), (True, False, False, False))

    def test_member_cannot_grant(self):
        r = self.client.post('/api/grants/', data=json.dumps(
            {'email': MEMBER, 'device': HP, 'speaker': True}),
            content_type='application/json', **self.member)
        self.assertEqual(r.status_code, 403)

    def test_delete_speaker_grant_kicks_publisher(self):
        g = self.speak_grant()
        self.assertEqual(self.publish(sid='mine'), 200)
        self.assertEqual(self.client.delete(f'/api/grants/{g.id}/', **self.admin).status_code, 204)
        self.assertEqual(self.fake.kicks(), ['/v1/sessions/webrtc/mine/kick'])
        self.assertFalse(MediaSession.objects.filter(id='mine').exists())

    def test_delete_other_grant_does_not_kick(self):
        self.speak_grant()
        other = self.speak_grant(email=OTHER)
        self.assertEqual(self.publish(sid='mine'), 200)
        self.client.delete(f'/api/grants/{other.id}/', **self.admin)
        self.assertEqual(self.fake.kicks(), [])

    def test_revoking_model_speaker_kicks_bridge_talker(self):
        g = self.speak_grant(email=MODEL)
        self.assertEqual(self.publish(token=self.ticket(), sid='w1'), 200)
        self.client.delete(f'/api/grants/{g.id}/', **self.admin)
        self.assertEqual(self.fake.kicks(), ['/v1/sessions/webrtc/w1/kick'])

    def test_forget_unplugged_speaker_kicks_talker(self):
        self.speak_grant()
        self.assertEqual(self.publish(sid='mine'), 200)
        self.fake.devices[-1].update(state='offline', connected=False)
        self.assertEqual(self.client.delete(f'/api/admin/devices/{HP}/', **self.admin).status_code,
                         204)
        self.assertIn('/v1/sessions/webrtc/mine/kick', self.fake.kicks())


# ── I2 / I5 — hook `publish` ─────────────────────────────────────────────────

class PublishHookTests(SpeakerCase):
    def test_jwt_with_right_allowed_and_session_kept(self):
        self.speak_grant()
        self.assertEqual(self.publish(sid='s-ok'), 200)
        row = MediaSession.objects.get(id='s-ok')
        self.assertEqual((row.email, row.path, row.protocol, row.bridge),
                         (MEMBER, TALK, 'webrtc', False))
        # « Bearer » en tête et chemin avec « / » initial : mêmes règles que la lecture.
        self.assertEqual(self.publish(path='/' + TALK, token='Bearer ' + make_token(MEMBER),
                                      sid='s-2'), 200)

    def test_jwt_refusals(self):
        self.speak_grant()
        self.grant(CAM, video=True)
        self.grant(MIC_CAM, video=True, audio=True)
        refusals = {
            'sans droit': dict(token=make_token(OTHER)),
            'jeton étranger': dict(token=make_token(MEMBER, foreign_key=True)),
            'mauvais azp': dict(token=make_token(MEMBER, azp='admin-cli')),
            'expiré': dict(token=make_token(MEMBER, exp_delta=-10)),
            'sans email': dict(token=make_token(None)),
            'jeton vide': dict(token=''),
            'rtsp': dict(protocol='rtsp'),
            'hls': dict(protocol='hls'),
            'srt': dict(protocol='srt'),
            'protocole vide': dict(protocol=''),
            'chemin webcam av': dict(path=f'webcam/{MIC_CAM}/av'),
            'chemin webcam video': dict(path=f'webcam/{CAM}/video'),
            'chemin webcam audio': dict(path=f'webcam/{MIC_CAM}/audio'),
            'ancienne webcam': dict(path='webcam/av'),
            'chemin inconnu': dict(path='speaker/hp-inconnu'),
            'sous-chemin': dict(path=TALK + '/x'),
            'préfixe': dict(path='speaker'),
            'chemin vide': dict(path=''),
            'sans id de session': dict(sid=''),
            'action api': dict(action='api'),
            'action playback': dict(action='playback'),
        }
        for name, kwargs in refusals.items():
            self.assertEqual(self.publish(**kwargs), 401, name)
        self.assertFalse(MediaSession.objects.exists())

    def test_jwt_non_string_fields_refused(self):
        self.speak_grant()
        for body in ({'token': 42}, {'path': ['x']}, {'id': 7}, {'protocol': None}):
            payload = {'action': 'publish', 'path': TALK, 'protocol': 'webrtc', 'id': 'x',
                       'token': make_token(MEMBER), **body}
            r = self.client.post(HOOK, data=payload, content_type='application/json')
            self.assertEqual(r.status_code, 401, body)

    def test_bad_hook_key_refused(self):
        self.speak_grant()
        body = {'action': 'publish', 'path': TALK, 'protocol': 'webrtc', 'id': 'x',
                'token': make_token(MEMBER)}
        r = self.client.post('/api/media-auth/?key=nope', data=body,
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_jwt_refused_during_cutoff(self):
        self.speak_grant()
        self.assertEqual(self.cut(True).status_code, 200)
        self.assertEqual(self.publish(), 401)
        self.cut(False)
        self.assertEqual(self.publish(sid='after'), 200)

    def test_video_right_does_not_give_speaker(self):
        Grant.objects.create(email=MEMBER, device_id=HP, video=True,
                             video_since=utc(2026, 11, 5), granted_by=ADMIN)   # incohérent en base
        self.assertEqual(self.publish(), 401)

    def test_incoherent_speaker_right_on_webcam_never_publishes(self):
        # Ligne impossible par l'API (400), insérée en base : le hook ne publie quand même que
        # sur le chemin `talk` d'un haut-parleur.
        Grant.objects.create(email=MEMBER, device_id=MIC_CAM, video=True, audio=True,
                             speaker=True, video_since=utc(2026, 11, 5), audio_since=utc(2026, 11, 5),
                             speaker_since=utc(2026, 11, 5), granted_by=ADMIN)
        for kind in ('av', 'video', 'audio'):
            self.assertEqual(self.publish(path=f'webcam/{MIC_CAM}/{kind}'), 401, kind)
        self.assertEqual(self.publish(path=f'webcam/{MIC_CAM}/av', token=self.ticket_for_member()),
                         401)

    def ticket_for_member(self):
        tok, _ = bridge.issue_ticket(MEMBER, VIEWER)
        return tok

    def test_speaker_right_on_other_speaker_refused(self):
        self.plug(speaker_item(HP2, model='Haut-parleur salon'))
        inventory.invalidate()
        self.admin_list()
        self.speak_grant(HP2)
        self.assertEqual(self.publish(path=TALK), 401)
        self.assertEqual(self.publish(path=TALK2, sid='p2'), 200)

    def test_speaker_since_null_refused(self):
        Grant.objects.create(email=MEMBER, device_id=HP, speaker=True, speaker_since=None,
                             granted_by=ADMIN)
        self.assertEqual(self.publish(), 401)

    def test_session_not_kept_refused(self):
        self.speak_grant()
        with mock.patch.object(MediaSession.objects, 'update_or_create',
                               side_effect=RuntimeError('db')):
            self.assertEqual(self.publish(), 401)

    def test_read_on_talk_path_refused(self):
        self.speak_grant()
        self.speak_grant(email=MODEL)
        for proto in ('webrtc', 'hls', 'rtsp'):
            self.assertEqual(self.publish(action='read', protocol=proto), 401, proto)
            self.assertEqual(self.publish(action='read', protocol=proto, token=self.ticket()),
                             401, proto)
        self.assertFalse(MediaSession.objects.exists())

    # ── Ticket (I5) ──
    def test_ticket_with_model_speaker_right_allowed_even_during_cutoff(self):
        self.speak_grant(email=MODEL)
        ticket = self.ticket()
        self.assertEqual(self.cut(True).status_code, 200)
        self.assertEqual(self.publish(token=ticket, sid='w1'), 200)
        row = MediaSession.objects.get(id='w1')
        self.assertEqual((row.email, row.viewer_email, row.bridge, row.path),
                         (MODEL, VIEWER, True, TALK))
        self.assertEqual(self.publish(token='Bearer ' + ticket, sid='w2'), 200)

    def test_ticket_refusals(self):
        self.speak_grant(email=MODEL)
        self.grant(CAM, email=MODEL, video=True)
        ticket = self.ticket()
        payload = signing.loads(ticket[4:], salt=bridge.TICKET_SALT)
        forged = 'pt1.' + signing.dumps(payload, salt='autre-sel')
        tampered = ticket[:-2] + ('A' if ticket[-1] != 'A' else 'B') + ticket[-1]
        refusals = {
            'protocole rtsp': dict(token=ticket, protocol='rtsp'),
            'protocole hls': dict(token=ticket, protocol='hls'),
            'chemin webcam': dict(token=ticket, path=f'webcam/{CAM}/video'),
            'chemin inconnu': dict(token=ticket, path='speaker/hp-autre'),
            'sans id': dict(token=ticket, sid=''),
            'falsifié': dict(token=tampered),
            'autre sel': dict(token=forged),
            'préfixe seul': dict(token='pt1.'),
            'préfixe et bruit': dict(token='pt1.abc'),
        }
        for name, kwargs in refusals.items():
            self.assertEqual(self.publish(**kwargs), 401, name)
        self.assertFalse(MediaSession.objects.exists())

    def test_ticket_of_model_without_speaker_right_refused(self):
        self.grant(CAM, email=MODEL, video=True)                 # ticket émis (webcam seule)
        ticket = self.ticket()
        self.speak_grant(email=OTHER)                            # droit de quelqu'un d'autre
        self.assertEqual(self.publish(token=ticket), 401)

    def test_ticket_right_checked_at_hook_time(self):
        g = self.speak_grant(email=MODEL)
        ticket = self.ticket()
        g.delete()
        self.assertEqual(self.publish(token=ticket), 401)

    def test_expired_ticket_refused(self):
        self.speak_grant(email=MODEL)
        ticket = self.ticket()
        with mock.patch('time.time', return_value=time.time() + bridge.TICKET_TTL_S + 2):
            self.assertEqual(self.publish(token=ticket), 401)
        with mock.patch('time.time', return_value=time.time() + bridge.TICKET_TTL_S - 2):
            self.assertEqual(self.publish(token=ticket, sid='just-in-time'), 200)
        exp = int(time.time()) - 1
        past = 'pt1.' + signing.dumps({'m': MODEL, 'v': VIEWER, 'exp': exp},
                                      salt=bridge.TICKET_SALT)
        self.assertEqual(self.publish(token=past), 401)


# ── Coupure — expulsion des publieurs par jeton Keycloak, pas du pont ─────────

class SpeakerCutoffTests(SpeakerCase):
    def test_cutoff_kicks_jwt_talkers_not_bridge_nor_capture(self):
        self.speak_grant()
        self.speak_grant(email=MODEL)
        self.assertEqual(self.publish(sid='jwt-talk'), 200)
        self.fake.sessions.clear()                    # un seul publieur à la fois (MediaMTX)
        self.assertEqual(self.publish(token=self.ticket(), sid='bridge-talk'), 200)
        # Publication de capture d'une webcam (RTSP) et publication WebRTC hors `speaker/` :
        # jamais expulsées par l'app.
        self.fake.sessions += [
            session_item('webrtc', 'jwt-talk', TALK, state='publish'),
            session_item('rtsp', 'capture', f'webcam/{CAM}/av', state='publish'),
            session_item('webrtc', 'odd-pub', f'webcam/{CAM}/av', state='publish'),
            session_item('webrtc', 'reader', f'webcam/{CAM}/video', state='read'),
        ]
        r = self.cut(True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['media_errors'], [])
        self.assertEqual(sorted(self.fake.kicks()),
                         ['/v1/sessions/webrtc/jwt-talk/kick', '/v1/sessions/webrtc/reader/kick'])
        self.assertTrue(MediaSession.objects.filter(id='bridge-talk').exists())
        self.assertFalse(MediaSession.objects.filter(id='jwt-talk').exists())

    def test_cutoff_kicks_unknown_jwt_talker(self):
        # Publieur dont la ligne n'existe pas (liste de lab-media seule) : expulsé quand même.
        self.fake.sessions.append(session_item('webrtc', 'ghost', TALK, state='publish'))
        self.cut(True)
        self.assertEqual(self.fake.kicks(), ['/v1/sessions/webrtc/ghost/kick'])

    def test_cutoff_never_touches_speaker_record(self):
        self.cut(True)
        self.assertFalse([c for c in self.device_calls() if c[0] == 'PATCH'])
        self.cut(False)
        self.assertFalse([c for c in self.device_calls() if c[0] == 'PATCH'])


# ── I4 — pont ────────────────────────────────────────────────────────────────

class SpeakerBridgeTests(SpeakerCase):
    def test_only_speaker_gives_ticket(self):
        self.speak_grant(email=MODEL)
        r = self.ask()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(list(body), ['model_email', 'devices', 'speakers', 'ticket', 'expires_at'])
        self.assertEqual(body['devices'], [])
        self.assertEqual(body['speakers'], [{'id': HP, 'label': 'Haut-parleur (prise jack)',
                                             'connected': True, 'state': 'idle',
                                             'paths': {'talk': TALK}}])
        self.assertTrue(body['ticket'].startswith('pt1.'))
        self.assertIsNotNone(body['expires_at'])
        self.assertEqual(bridge.read_ticket(body['ticket']), (MODEL, VIEWER))

    def test_speakers_sorted_and_filtered_cameras_unchanged(self):
        self.plug(speaker_item(HP2, model='Atelier'))
        inventory.invalidate()
        self.admin_list()
        self.speak_grant(email=MODEL)
        self.speak_grant(HP2, email=MODEL)
        self.speak_grant(email=OTHER)
        self.grant(CAM, email=MODEL, video=True)
        body = self.ask().json()
        self.assertEqual([s['id'] for s in body['speakers']], [HP2, HP])
        self.assertEqual([d['id'] for d in body['devices']], [CAM])
        # Jamais un haut-parleur dans `devices`, jamais une webcam dans `speakers`.
        self.assertTrue(all(set(s['paths']) == {'talk'} for s in body['speakers']))

    def test_speakers_always_present_empty(self):
        self.speak_grant(email=OTHER)
        body = self.ask().json()
        self.assertEqual(body, {'model_email': MODEL, 'devices': [], 'speakers': [],
                                'ticket': None, 'expires_at': None})

    def test_cutoff_has_no_effect_on_speakers(self):
        self.speak_grant(email=MODEL)
        before = self.ask().json()['speakers']
        self.cut(True)
        self.assertEqual(self.ask().json()['speakers'], before)

    def test_offline_speaker_still_listed_with_state(self):
        self.speak_grant(email=MODEL)
        self.fake.devices[-1].update(state='offline', connected=False)
        inventory.invalidate()
        self.admin_list()
        self.assertEqual(self.ask().json()['speakers'][0], {'id': HP, 'label': 'Haut-parleur (prise jack)',
                                'connected': False, 'state': 'offline', 'paths': {'talk': TALK}})
