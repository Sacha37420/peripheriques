"""Chantier « webcams » (contrat v1) : inventaire I1, gestion des périphériques, hook I2 par
chemins connus, migration `webcam` → `webcam-ancienne`.

Le faux lab-media (`fake_lab_media.py`) rend les objets I1 recopiés du contrat ; le client
`api.media_client` est donc exercé de bout en bout (URL, Basic, corps, codes).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings

from api import inventory, keycloak_admin
from api.models import Device, Grant, MediaSession

from .fake_lab_media import FakeLabMedia, closed_port_url, device_item, session_item
from .helpers import ADMIN, MEMBER, OTHER, admin_token, bearer, make_token, patch_jwks

CAM = 'usb-32e4-9230-p3-4'                     # exemple réel du contrat (sans série, port 3-4)
MIC_CAM = 'usb-046d-0825-s1a2b3c4d'             # webcam avec micro et numéro de série
HOOK = '/api/media-auth/?key=hook-key-for-tests'


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class WebcamCase(TestCase):
    def setUp(self):
        self.fake = FakeLabMedia()
        self.addCleanup(self.fake.close)
        override = override_settings(MEDIA_API_URL=self.fake.url)
        override.enable()
        self.addCleanup(override.disable)
        jwks = patch_jwks()
        jwks.start()
        self.addCleanup(jwks.stop)
        inventory.invalidate()
        self.addCleanup(inventory.invalidate)
        self.admin = bearer(admin_token())
        self.member = bearer(make_token(MEMBER))

    def plug(self, *items):
        self.fake.devices.extend(items)

    def admin_list(self, headers=None):
        return self.client.get('/api/admin/devices/', **(self.admin if headers is None else headers))

    def device_calls(self):
        return [c for c in self.fake.calls if c[1].startswith('/v1/devices')]

    def grant(self, slug, email=MEMBER, video=True, audio=False, since=None):
        since = since or utc(2026, 10, 17, 8)
        return Grant.objects.create(email=email, device_id=slug, video=video, audio=audio,
                                    video_since=since if video else None,
                                    audio_since=since if audio else None, granted_by=ADMIN)

    def hook(self, path, token=None, sid='s1', protocol='webrtc'):
        body = {'ip': '192.168.48.3', 'user': '', 'password': '', 'token': token or make_token(MEMBER),
                'action': 'read', 'path': path, 'protocol': protocol, 'id': sid, 'query': '',
                'userAgent': 'Mozilla/5.0'}
        r = self.client.post(HOOK, data=body, content_type='application/json')
        if r.status_code == 200:
            self.fake.sessions.append(session_item(protocol, sid, path))
        return r.status_code

    def patch_device(self, slug, body, headers=None):
        return self.client.patch(f'/api/admin/devices/{slug}/', data=json.dumps(body),
                                 content_type='application/json',
                                 **(self.admin if headers is None else headers))


# ── Synchronisation de l'inventaire ──────────────────────────────────────────

class InventorySyncTests(WebcamCase):
    def test_new_device_appears_everywhere_without_human_action(self):
        self.plug(device_item(CAM))
        r = self.client.get('/api/grants/devices/', **self.admin)
        self.assertEqual(r.status_code, 200)
        by_slug = {d['slug']: d for d in r.json()}
        self.assertEqual(set(by_slug), {'webcam-ancienne', CAM})
        self.assertEqual(by_slug[CAM], {'slug': CAM, 'label': 'HD USB Camera', 'online': True,
                                        'has_audio': False, 'state': 'live', 'connected': True})
        d = Device.objects.get(slug=CAM)
        self.assertEqual((d.model, d.vendor_id, d.product_id, d.usb_port, d.serial),
                         ('HD USB Camera', '32e4', '9230', '3-4', ''))
        self.assertEqual(d.paths(), {'av': f'webcam/{CAM}/av', 'video': f'webcam/{CAM}/video',
                                     'audio': None})
        self.assertEqual(d.first_seen, utc(2026, 10, 16, 22, 21, 4))
        # « online » vient de l'état réel du chemin av donné par I1
        self.assertIn(('GET', f'/v1/paths/webcam/{CAM}/av', {}, True), self.fake.calls)

    def test_admin_list_full_description_in_first_seen_order(self):
        later = device_item(MIC_CAM, model='Webcam C270', has_audio=True, state='waiting',
                            usb_port='1-2')
        later['first_seen'] = '2026-10-17T05:00:00+02:00'
        self.plug(later, device_item(CAM))
        r = self.admin_list()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['inventory']['reachable'], True)
        self.assertEqual([d['id'] for d in body['items']], ['webcam-ancienne', CAM, MIC_CAM])
        cam = body['items'][1]
        self.assertEqual(cam, {
            'id': CAM, 'label': 'HD USB Camera', 'model': 'HD USB Camera',
            'vendor_id': '32e4', 'product_id': '9230', 'serial': '', 'usb_port': '3-4',
            'has_audio': False, 'connected': True, 'state': 'live', 'error_detail': '',
            'record': False, 'first_seen': '2026-10-16T22:21:04Z',
            'last_seen': '2026-10-17T07:12:40Z',
            'paths': {'av': f'webcam/{CAM}/av', 'video': f'webcam/{CAM}/video', 'audio': None},
            'listed': True, 'grants': 0,
        })
        mic = body['items'][2]
        self.assertEqual((mic['state'], mic['has_audio'], mic['paths']['audio']),
                         ('waiting', True, f'webcam/{MIC_CAM}/audio'))
        legacy = body['items'][0]
        self.assertEqual((legacy['label'], legacy['state'], legacy['connected']),
                         ('Ancienne webcam', 'legacy', False))

    def test_cache_short_then_refresh(self):
        with self.settings(DEVICE_SYNC_TTL_S=60):
            self.admin_list()
            self.admin_list()
            self.client.get('/api/grants/devices/', **self.admin)
            self.assertEqual(len(self.device_calls()), 1)
            self.plug(device_item(CAM))
            self.assertNotIn(CAM, [d['id'] for d in self.admin_list().json()['items']])
            inventory.invalidate()
            self.assertIn(CAM, [d['id'] for d in self.admin_list().json()['items']])

    def test_lab_media_down_keeps_known_devices(self):
        self.plug(device_item(CAM))
        self.admin_list()
        g = self.grant(CAM)
        for broken in (override_settings(MEDIA_API_URL=closed_port_url()),
                       override_settings(MEDIA_INTERNAL_PASSWORD='faux')):
            with broken, self.assertLogs('api.inventory', 'WARNING'):
                inventory.invalidate()
                r = self.admin_list()
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()['inventory']['reachable'], False)
            self.assertIn(CAM, [d['id'] for d in r.json()['items']])
        self.fake.devices_status = 502                 # media_unavailable
        with self.assertLogs('api.inventory', 'WARNING'):
            inventory.invalidate()
            self.assertEqual(len(self.admin_list().json()['items']), 2)
        self.assertTrue(Grant.objects.filter(pk=g.pk).exists())
        # Et la lecture continue de fonctionner sur ce qui est connu (hook = base seule).
        self.assertEqual(self.hook(f'webcam/{CAM}/video'), 200)

    def test_route_absente_avant_la_mise_en_service_de_lab_media(self):
        # lab-media actuel : /v1/devices n'existe pas (404 not_found) ⇒ rien ne bouge.
        self.fake.devices_status = 404
        with self.assertLogs('api.inventory', 'WARNING'):
            r = self.client.get('/api/grants/devices/', **self.admin)
        self.assertEqual([d['slug'] for d in r.json()], ['webcam-ancienne'])

    def test_label_never_overwritten_by_sync(self):
        self.plug(device_item(CAM))
        self.admin_list()
        self.assertEqual(self.patch_device(CAM, {'label': 'Salon'}).status_code, 200)
        self.fake.devices[-1]['model'] = 'Autre nom produit'
        inventory.invalidate()
        item = self.admin_list().json()['items'][1]
        self.assertEqual((item['label'], item['model']), ('Salon', 'Autre nom produit'))

    def test_state_follows_inventory(self):
        self.plug(device_item(CAM))
        self.admin_list()
        dev = self.fake.devices[-1]
        dev.update(state='error', error_detail='Capture en échec : périphérique occupé.',
                   connected=True)
        inventory.invalidate()
        item = self.admin_list().json()['items'][1]
        self.assertEqual((item['state'], item['error_detail']),
                         ('error', 'Capture en échec : périphérique occupé.'))
        dev.update(state='offline', connected=False, error_detail='x')
        inventory.invalidate()
        item = self.admin_list().json()['items'][1]
        self.assertEqual((item['state'], item['connected'], item['error_detail']),
                         ('offline', False, ''))

    def test_model_is_plain_text(self):
        evil = '<img src=x onerror=alert(1)>\u0007Cam\n'
        self.plug(device_item(CAM, model=evil))
        item = self.admin_list().json()['items'][1]
        # Stocké et rendu tel quel (sans caractère de contrôle) : c'est au rendu de l'échapper.
        self.assertEqual(item['model'], '<img src=x onerror=alert(1)>Cam')
        self.assertEqual(item['label'], '<img src=x onerror=alert(1)>Cam')

    def test_malformed_entries_ignored_others_kept(self):
        bad_id = device_item(CAM)
        bad_id['id'] = '../../etc'
        no_paths = device_item('usb-1111-2222-p1-1')
        no_paths['paths'] = None
        bad_path = device_item('usb-1111-2222-p1-2')
        bad_path['paths']['video'] = 'webcam/../x'
        same_path = device_item('usb-1111-2222-p1-3')
        same_path['paths']['video'] = same_path['paths']['av']
        self.plug(bad_id, no_paths, bad_path, same_path, 'pas un objet', device_item(MIC_CAM))
        with self.assertLogs('api.inventory', 'WARNING'):
            ids = [d['id'] for d in self.admin_list().json()['items']]
        self.assertEqual(ids, ['webcam-ancienne', MIC_CAM])

    def test_path_already_owned_by_another_device_is_refused(self):
        thief = device_item(CAM)
        thief['paths']['video'] = 'webcam/video'      # chemin de l'ancienne webcam
        self.plug(thief)
        self.grant('webcam-ancienne', video=True)
        with self.assertLogs('api.inventory', 'WARNING'):
            ids = [d['id'] for d in self.admin_list().json()['items']]
        self.assertNotIn(CAM, ids)
        self.assertEqual(Device.by_path('webcam/video')[0].slug, 'webcam-ancienne')

    def test_absent_from_inventory_is_kept_but_marked(self):
        self.plug(device_item(CAM))
        self.admin_list()
        self.grant(CAM)
        self.fake.devices.pop()
        inventory.invalidate()
        item = next(d for d in self.admin_list().json()['items'] if d['id'] == CAM)
        self.assertEqual((item['listed'], item['grants']), (False, 1))


# ── Gestion réservée aux administrateurs des droits ──────────────────────────

@mock.patch('api.media_client.list_sessions', return_value=[])
class AdminDeviceTests(WebcamCase):
    def setUp(self):
        super().setUp()
        self.plug(device_item(CAM))
        self.admin_list()
        self.fake.calls.clear()

    def test_non_admin_403_on_every_route(self, _live):
        member = self.member
        cli = bearer(make_token(ADMIN, groups=('developers',), azp='admin-cli'))
        for headers in (member, cli, {}):
            self.assertIn(self.admin_list(headers).status_code, (401, 403))
            self.assertIn(self.client.get(f'/api/admin/devices/{CAM}/', **headers).status_code,
                          (401, 403))
            self.assertIn(self.patch_device(CAM, {'label': 'X', 'record': True}, headers).status_code,
                          (401, 403))
            self.assertIn(self.client.delete(f'/api/admin/devices/{CAM}/', **headers).status_code,
                          (401, 403))
        self.assertEqual(self.patch_device(CAM, {'label': 'X'}, member).status_code, 403)
        self.assertEqual(Device.objects.get(slug=CAM).label, 'HD USB Camera')
        self.assertEqual(self.device_calls(), [])      # rien relayé à lab-media
        self.assertTrue(Device.objects.filter(slug=CAM).exists())

    def test_rename(self, _live):
        r = self.patch_device(CAM, {'label': '  Caméra du salon  '})
        self.assertEqual((r.status_code, r.json()['label']), (200, 'Caméra du salon'))
        self.assertEqual(Device.objects.get(slug=CAM).label, 'Caméra du salon')
        self.assertEqual(self.device_calls(), [])      # le nom ne concerne pas lab-media
        self.assertEqual(self.patch_device(CAM, {'label': 'x' * 60}).status_code, 200)
        self.assertEqual(self.patch_device(CAM, {'label': '<b>gras</b>'}).json()['label'],
                         '<b>gras</b>')

    def test_rename_refusals(self, _live):
        for bad in ('', '   ', 'x' * 61, 'deux\nlignes', 'tab\tulation', None, 12, ['a']):
            r = self.patch_device(CAM, {'label': bad})
            self.assertEqual(r.status_code, 400, bad)
        for body in ({}, {'nom': 'x'}, {'label': 'x', 'id': 'autre'}, ['label']):
            self.assertEqual(self.patch_device(CAM, body).status_code, 400, body)
        self.assertEqual(self.patch_device('inconnu', {'label': 'x'}).status_code, 404)
        self.assertEqual(Device.objects.get(slug=CAM).label, 'HD USB Camera')

    def test_record_switch_relayed(self, _live):
        r = self.patch_device(CAM, {'record': True})
        self.assertEqual((r.status_code, r.json()['record']), (200, True))
        self.assertEqual(self.device_calls()[-1][:2], ('PATCH', f'/v1/devices/{CAM}'))
        self.assertTrue(self.device_calls()[-1][3])                   # identifiants de contrôle
        self.assertEqual(self.fake.bodies, [{'record': True}])
        self.assertTrue(Device.objects.get(slug=CAM).record)
        r = self.patch_device(CAM, {'record': False, 'label': 'Entrée'})
        self.assertEqual((r.json()['record'], r.json()['label']), (False, 'Entrée'))
        self.assertEqual(self.fake.bodies[-1], {'record': False})
        for bad in ('true', 1, None):
            self.assertEqual(self.patch_device(CAM, {'record': bad}).status_code, 400)
        self.assertEqual(len(self.fake.bodies), 2)

    def test_record_legacy_409(self, _live):
        r = self.patch_device('webcam-ancienne', {'record': True})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(Device.objects.get(slug='webcam-ancienne').record)

    def test_record_media_down_saves_nothing(self, _live):
        with override_settings(MEDIA_API_URL=closed_port_url()), self.assertLogs('api.views'):
            r = self.patch_device(CAM, {'record': True, 'label': 'Pas enregistré'})
        self.assertEqual((r.status_code, r.json()), (503, {'detail': 'Serveur média indisponible.'}))
        d = Device.objects.get(slug=CAM)
        self.assertEqual((d.record, d.label), (False, 'HD USB Camera'))

    def test_record_unknown_to_lab_media_404(self, _live):
        self.fake.devices.pop()
        self.assertEqual(self.patch_device(CAM, {'record': True}).status_code, 404)

    def test_forget_connected_refused(self, _live):
        r = self.client.delete(f'/api/admin/devices/{CAM}/', **self.admin)
        self.assertEqual(r.status_code, 409)
        self.assertTrue(Device.objects.filter(slug=CAM).exists())

    def test_forget_unplugged_removes_device_grants_and_kicks(self, live):
        self.fake.devices[-1].update(connected=False, state='offline')
        self.grant(CAM, email=MEMBER)
        self.grant(CAM, email=OTHER)
        self.grant('webcam-ancienne', email=MEMBER)
        for sid, path in (('w1', f'webcam/{CAM}/video'), ('h1', f'webcam/{CAM}/av')):
            MediaSession.objects.create(id=sid, email=MEMBER, path=path,
                                        protocol='webrtc' if sid == 'w1' else 'hls')
        MediaSession.objects.create(id='h2', email=OTHER, path=f'webcam/{CAM}/video', protocol='hls')
        MediaSession.objects.create(id='old', email=MEMBER, path='webcam/video', protocol='webrtc')
        live.side_effect = lambda p: [{'id': i} for i in ('w1', 'h1', 'h2', 'old')]
        with mock.patch('api.media_client.kick_session') as kick:
            r = self.client.delete(f'/api/admin/devices/{CAM}/', **self.admin)
        self.assertEqual(r.status_code, 204)
        self.assertIn(('DELETE', f'/v1/devices/{CAM}', {}, True), self.fake.calls)
        self.assertFalse(Device.objects.filter(slug=CAM).exists())
        self.assertEqual(list(Grant.objects.values_list('device_id', flat=True)), ['webcam-ancienne'])
        # WebRTC et HLS de tout le monde sur CE périphérique ; rien sur l'ancienne webcam.
        self.assertEqual(sorted(c.args for c in kick.call_args_list),
                         [('hls', 'h1'), ('hls', 'h2'), ('webrtc', 'w1')])
        self.assertEqual(list(MediaSession.objects.values_list('id', flat=True)), ['old'])
        # Oublié chez lab-media aussi : il ne revient pas à la synchronisation suivante.
        inventory.invalidate()
        self.assertNotIn(CAM, [d['id'] for d in self.admin_list().json()['items']])

    def test_forget_already_forgotten_by_lab_media(self, _live):
        self.fake.devices.pop()                         # lab-media répond 404
        self.assertEqual(self.client.delete(f'/api/admin/devices/{CAM}/', **self.admin).status_code, 204)
        self.assertFalse(Device.objects.filter(slug=CAM).exists())

    def test_forget_media_down_keeps_everything(self, _live):
        self.fake.devices[-1].update(connected=False, state='offline')
        self.grant(CAM)
        with override_settings(MEDIA_API_URL=closed_port_url()), self.assertLogs('api.views'):
            r = self.client.delete(f'/api/admin/devices/{CAM}/', **self.admin)
        self.assertEqual(r.status_code, 503)
        self.assertTrue(Grant.objects.filter(device_id=CAM).exists())

    def test_forget_legacy_allowed(self, _live):
        self.grant('webcam-ancienne')
        r = self.client.delete('/api/admin/devices/webcam-ancienne/', **self.admin)
        self.assertEqual(r.status_code, 204)
        self.assertFalse(Grant.objects.exists())


# ── Droits : vidéo seule pour une webcam sans micro ───────────────────────────

@mock.patch.object(keycloak_admin, 'find_user_by_email', return_value={'id': 'u1'})
class NoMicGrantTests(WebcamCase):
    def setUp(self):
        super().setUp()
        self.plug(device_item(CAM), device_item(MIC_CAM, has_audio=True))
        self.admin_list()

    def post(self, body):
        return self.client.post('/api/grants/', data=body, content_type='application/json',
                                **self.admin)

    def test_audio_refused_on_device_without_mic(self, _kc):
        r = self.post({'email': MEMBER, 'device': CAM, 'video': True, 'audio': True})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'audio': ["Ce périphérique n'a pas de micro : seul le droit "
                                              "Vidéo peut être accordé."]})
        self.assertEqual(self.post({'email': MEMBER, 'device': CAM, 'audio': True}).status_code, 400)
        r = self.post({'email': MEMBER, 'device': CAM, 'video': True})
        self.assertEqual(r.status_code, 201)
        p = self.client.patch(f"/api/grants/{r.json()['id']}/", data={'audio': True},
                              content_type='application/json', **self.admin)
        self.assertEqual(p.status_code, 400)
        self.assertFalse(Grant.objects.get(device_id=CAM).audio)

    def test_audio_allowed_on_device_with_mic(self, _kc):
        r = self.post({'email': MEMBER, 'device': MIC_CAM, 'video': True, 'audio': True})
        self.assertEqual(r.status_code, 201)


# ── Direct, hook (I2) et relecture par les chemins donnés par I1 ─────────────

class PathTests(WebcamCase):
    def setUp(self):
        super().setUp()
        self.plug(device_item(CAM), device_item(MIC_CAM, has_audio=True))
        self.admin_list()

    def test_member_list_gives_paths_of_i1(self):
        self.grant(MIC_CAM, video=True, audio=True)
        d = self.client.get('/api/devices/', **self.member).json()
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]['paths'], {'av': f'webcam/{MIC_CAM}/av',
                                         'video': f'webcam/{MIC_CAM}/video',
                                         'audio': f'webcam/{MIC_CAM}/audio'})
        self.assertEqual((d[0]['label'], d[0]['has_audio'], d[0]['online']),
                         ('HD USB Camera', True, True))

    def test_hook_finds_device_by_known_paths(self):
        self.grant(CAM, video=True)
        self.assertEqual(self.hook(f'webcam/{CAM}/video'), 200)
        self.assertEqual(self.hook(f'/webcam/{CAM}/video', sid='s2'), 200)
        self.assertEqual(MediaSession.objects.get(id='s1').path, f'webcam/{CAM}/video')
        self.assertEqual(self.hook(f'webcam/{CAM}/av', sid='s3'), 401)       # av = vidéo + micro
        # Un droit sur CAM ne donne rien sur MIC_CAM, ni sur l'ancienne webcam.
        self.assertEqual(self.hook(f'webcam/{MIC_CAM}/video', sid='s4'), 401)
        self.assertEqual(self.hook('webcam/video', sid='s5'), 401)
        # Chemins inconnus : jamais déduits en découpant la chaîne.
        for path in (f'webcam/{CAM}/audio', f'webcam/{CAM}/raw', f'webcam/{CAM}', f'{CAM}/video',
                     f'webcam/{CAM}/video/x', f'autre/{CAM}/video', 'webcam/', ''):
            self.assertEqual(self.hook(path, sid='x'), 401, path)

    def test_legacy_paths_keep_working_for_legacy_grants(self):
        self.grant('webcam-ancienne', video=True, audio=True)
        self.assertEqual(self.hook('webcam/av'), 200)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='s2'), 401)

    def test_ambiguous_path_refused(self):
        # Ne peut pas arriver par la synchronisation (refusé) ; si la base le contenait, refus.
        Device.objects.filter(slug=MIC_CAM).update(path_video=f'webcam/{CAM}/video')
        self.grant(CAM, video=True)
        self.assertEqual(self.hook(f'webcam/{CAM}/video'), 401)

    @mock.patch.object(keycloak_admin, 'find_user_by_email', return_value={'id': 'u1'})
    def test_revocation_kicks_this_device_only(self, _kc):
        g_cam = self.grant(CAM, video=True)
        g_old = self.grant('webcam-ancienne', video=True, audio=True)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='cam-hls', protocol='hls'), 200)
        self.assertEqual(self.hook(f'webcam/{CAM}/video', sid='cam-rtc'), 200)
        self.assertEqual(self.hook('webcam/video', sid='old-rtc'), 200)
        # Révoquer l'ancienne webcam ne touche pas aux lectures de CAM (pas de préfixe « webcam/ »).
        self.assertEqual(self.client.delete(f'/api/grants/{g_old.id}/', **self.admin).status_code, 204)
        self.assertEqual(self.fake.kicks(), ['/v1/sessions/webrtc/old-rtc/kick'])
        self.assertEqual(self.client.delete(f'/api/grants/{g_cam.id}/', **self.admin).status_code, 204)
        self.assertEqual(sorted(self.fake.kicks()[1:]),
                         ['/v1/sessions/hls/cam-hls/kick', '/v1/sessions/webrtc/cam-rtc/kick'])
        self.assertFalse(MediaSession.objects.exists())

    def test_recordings_use_av_path_of_i1(self):
        self.grant(CAM, video=True, since=utc(2026, 10, 17, 8))
        self.fake.recordings[f'webcam/{CAM}/av'] = [{'start': '2026-10-17T07:30:00Z', 'duration': 3600.0}]
        r = self.client.get('/api/recordings/', {'device': CAM, 'date': '2026-10-17'}, **self.member)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(s['start'], s['tracks']) for s in r.json()],
                         [('2026-10-17T08:00:00Z', ['video'])])     # toujours borné au since
        self.assertEqual(self.fake.calls[-1][:3], ('GET', '/v1/recordings', {'path': f'webcam/{CAM}/av'}))
        self.fake.clip = b'\x00\x00\x00\x18ftypiso5' + b'v' * 1000
        t = self.client.post('/api/recordings/ticket/', {'device': CAM, 'start': '2026-10-17T08:00:00Z',
                                                         'duration_s': 60},
                             content_type='application/json', **self.member)
        self.assertEqual(t.status_code, 200)
        self.assertEqual(self.client.post('/api/recordings/ticket/', {
            'device': CAM, 'start': '2026-10-17T07:59:59Z', 'duration_s': 60},
            content_type='application/json', **self.member).status_code, 403)
        with mock.patch('api.recordings._Remux.__iter__', return_value=iter([b'ok'])):
            s = self.client.get(t.json()['url'][len('/peripheriques-api'):])
            self.assertEqual(s.status_code, 200)
            b''.join(s.streaming_content)
        self.assertEqual(self.fake.calls[-1][2]['path'], f'webcam/{CAM}/av')


# ── Migration `webcam` → `webcam-ancienne` ───────────────────────────────────

class MigrationTests(TransactionTestCase):
    before = [('api', '0002_seed_webcam')]
    after = [('api', '0004_webcam_ancienne')]

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(target)
        return executor.loader.project_state(target).apps

    def tearDown(self):
        self.migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())

    def test_webcam_becomes_webcam_ancienne_with_its_grants(self):
        apps = self.migrate(self.before)
        Device, Grant = apps.get_model('api', 'Device'), apps.get_model('api', 'Grant')
        self.assertEqual(list(Device.objects.values_list('slug', flat=True)), ['webcam'])
        t1, t2 = utc(2026, 10, 10, 9, 0, 1), utc(2026, 10, 12, 18, 30)
        Grant.objects.create(email='sacha@ssolab.local', device_id='webcam', video=True, audio=True,
                             video_since=t1, audio_since=t2, granted_by=ADMIN)
        Grant.objects.create(email=MEMBER, device_id='webcam', video=True, video_since=t2,
                             granted_by=ADMIN)

        apps = self.migrate(self.after)
        Device, Grant = apps.get_model('api', 'Device'), apps.get_model('api', 'Grant')
        d = Device.objects.get()
        self.assertEqual((d.slug, d.label, d.state, d.has_audio, d.record, d.connected),
                         ('webcam-ancienne', 'Ancienne webcam', 'legacy', True, False, False))
        self.assertEqual((d.path_av, d.path_video, d.path_audio),
                         ('webcam/av', 'webcam/video', 'webcam/audio'))
        rows = sorted(Grant.objects.values_list('email', 'device_id', 'video', 'audio',
                                                'video_since', 'audio_since'))
        self.assertEqual(rows, [
            (MEMBER, 'webcam-ancienne', True, False, t2, None),
            ('sacha@ssolab.local', 'webcam-ancienne', True, True, t1, t2),
        ])

        apps = self.migrate(self.before)                 # retour arrière : même état qu'avant
        Grant = apps.get_model('api', 'Grant')
        self.assertEqual(sorted(Grant.objects.values_list('device_id', flat=True)), ['webcam', 'webcam'])
