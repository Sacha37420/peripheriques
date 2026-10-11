"""Relecture (I3) : liste clippée aux since, ticket signé, flux re-vérifié.

La relecture de lab-media est simulée (patch de `api.media_client.playback_list` /
`playback_get` ; le client réel est exercé dans `test_lab_media.py`). Le remux, lui, est exécuté **en réel** :
un vrai fMP4 H.264 + Opus est généré par ffmpeg puis passé dans la vue, et la
sortie est inspectée par ffprobe.

    python manage.py test api.tests.test_recordings --settings=config.settings_test
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.core import signing
from django.test import TestCase

from api import recordings
from api.media_client import MediaError
from api.models import Grant

from .helpers import ADMIN, MEMBER, admin_token, bearer, make_token, patch_jwks

LIST_URL = '/api/recordings/'
TICKET_URL = '/api/recordings/ticket/'
STREAM_PATH = '/api/recordings/stream/'


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class _FakeUpstream:
    """Imite la `requests.Response` en flux renvoyée par `playback_get`."""

    def __init__(self, data: bytes):
        self.data = data
        self.closed = False

    def iter_content(self, chunk_size):
        for i in range(0, len(self.data), chunk_size):
            if self.closed:
                return
            yield self.data[i:i + chunk_size]

    def close(self):
        self.closed = True


def _ffprobe_streams(path: Path) -> list[dict]:
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)['streams']


class RecordingsBase(TestCase):
    def setUp(self):
        # Patch posé dans setUp : un décorateur de classe ne couvrirait pas les
        # méthodes des sous-classes.
        jwks = patch_jwks()
        jwks.start()
        self.addCleanup(jwks.stop)
        self.member = bearer(make_token(MEMBER))
        self.admin = bearer(admin_token())

    def grant(self, video_since=None, audio_since=None, email=MEMBER):
        return Grant.objects.create(
            email=email, device_id='webcam',
            video=video_since is not None, audio=audio_since is not None,
            video_since=video_since, audio_since=audio_since, granted_by=ADMIN,
        )

    def list(self, ranges, headers, date='2026-10-04', device='webcam'):
        with mock.patch('api.media_client.playback_list', return_value=ranges) as pl:
            resp = self.client.get(LIST_URL, {'device': device, 'date': date}, **headers)
        return resp, pl

    def ticket(self, headers, start='2026-10-04T11:00:00Z', duration_s=600, device='webcam'):
        return self.client.post(TICKET_URL, {'device': device, 'start': start,
                                             'duration_s': duration_s},
                                content_type='application/json', **headers)

    def stream_path(self, ticket_resp) -> str:
        url = ticket_resp.json()['url']
        self.assertTrue(url.startswith('/peripheriques-api/api/recordings/stream/?ticket='))
        return url[len('/peripheriques-api'):]


class RecordingListTests(RecordingsBase):
    def test_clipping_au_since_et_pistes(self):
        self.grant(video_since=utc(2026, 10, 4, 10, 15), audio_since=utc(2026, 10, 4, 10, 45))
        resp, pl = self.list([{'start': '2026-10-04T09:30:00Z', 'duration': 7200.0}],
                             self.member)
        self.assertEqual(resp.status_code, 200, resp.content)
        pl.assert_called_once_with('webcam/av')
        self.assertEqual(resp.json(), [
            # 09:30–10:15 : aucune piste ⇒ absent ; le morceau chevauchant commence au since.
            {'start': '2026-10-04T10:15:00Z', 'end': '2026-10-04T10:45:00Z',
             'duration_s': 1800, 'tracks': ['video']},
            # audio n'apparaît qu'à partir de son propre since.
            {'start': '2026-10-04T10:45:00Z', 'end': '2026-10-04T11:00:00Z',
             'duration_s': 900, 'tracks': ['video', 'audio']},
            {'start': '2026-10-04T11:00:00Z', 'end': '2026-10-04T11:30:00Z',
             'duration_s': 1800, 'tracks': ['video', 'audio']},
        ])

    def test_plage_entierement_avant_since_absente(self):
        self.grant(video_since=utc(2026, 10, 4, 15))
        resp, _ = self.list([{'start': '2026-10-04T08:00:00Z', 'duration': 3600},
                             {'start': '2026-10-04T15:30:00Z', 'duration': 60}], self.member)
        self.assertEqual(resp.json(), [
            {'start': '2026-10-04T15:30:00Z', 'end': '2026-10-04T15:31:00Z',
             'duration_s': 60, 'tracks': ['video']},
        ])

    def test_decoupe_horaire_et_tri(self):
        self.grant(video_since=utc(2026, 1, 1), audio_since=utc(2026, 1, 1))
        resp, _ = self.list([
            {'start': '2026-10-04T13:20:00.5Z', 'duration': 2 * 3600},
            {'start': '2026-10-04T06:00:00Z', 'duration': 30},
        ], self.member)
        data = resp.json()
        self.assertEqual([d['start'] for d in data], [
            '2026-10-04T06:00:00Z', '2026-10-04T13:20:00.500000Z',
            '2026-10-04T14:00:00Z', '2026-10-04T15:00:00Z'])
        self.assertEqual([d['end'] for d in data], [
            '2026-10-04T06:00:30Z', '2026-10-04T14:00:00Z',
            '2026-10-04T15:00:00Z', '2026-10-04T15:20:00.500000Z'])
        for d in data:
            self.assertLessEqual(d['duration_s'], 3600)
            self.assertEqual(d['tracks'], ['video', 'audio'])

    def test_nanosecondes_mediamtx(self):
        self.grant(video_since=utc(2026, 1, 1))
        resp, _ = self.list([{'start': '2026-10-04T12:00:00.123456789+02:00',
                              'duration': 10.5}], self.member)
        self.assertEqual(resp.json(), [{'start': '2026-10-04T10:00:00.123456Z',
                                        'end': '2026-10-04T10:00:10.623456Z',
                                        'duration_s': 11, 'tracks': ['video']}])

    def test_journee_europe_paris(self):
        # 2026-10-04 (heure d'été) = [2026-10-03T22:00Z, 2026-10-04T22:00Z).
        self.grant(video_since=utc(2020, 1, 1), audio_since=utc(2020, 1, 1))
        resp, _ = self.list([{'start': '2026-10-02T00:00:00Z', 'duration': 5 * 86400}],
                            self.member)
        data = resp.json()
        self.assertEqual(len(data), 24)
        self.assertEqual(data[0]['start'], '2026-10-03T22:00:00Z')
        self.assertEqual(data[-1]['end'], '2026-10-04T22:00:00Z')

    def test_jour_de_25h_changement_heure(self):
        # 2026-10-25 : passage à l'heure d'hiver ⇒ [2026-10-24T22:00Z, 2026-10-25T23:00Z).
        self.grant(video_since=utc(2020, 1, 1), audio_since=utc(2020, 1, 1))
        resp, _ = self.list([{'start': '2026-10-24T20:00:00Z', 'duration': 30 * 3600}],
                            self.member, date='2026-10-25')
        data = resp.json()
        self.assertEqual(len(data), 25)
        self.assertEqual(data[0]['start'], '2026-10-24T22:00:00Z')
        self.assertEqual(data[-1]['end'], '2026-10-25T23:00:00Z')
        self.assertEqual(sum(d['duration_s'] for d in data), 25 * 3600)

    def test_jour_de_23h_changement_heure(self):
        # 2026-03-29 : passage à l'heure d'été ⇒ [2026-03-28T23:00Z, 2026-03-29T22:00Z).
        self.grant(video_since=utc(2020, 1, 1), audio_since=utc(2020, 1, 1))
        resp, _ = self.list([{'start': '2026-03-28T00:00:00Z', 'duration': 3 * 86400}],
                            self.member, date='2026-03-29')
        data = resp.json()
        self.assertEqual(len(data), 23)
        self.assertEqual(data[0]['start'], '2026-03-28T23:00:00Z')
        self.assertEqual(data[-1]['end'], '2026-03-29T22:00:00Z')

    def test_admin_sans_grant_403(self):
        # v3 : être admin ne donne aucun droit de relecture.
        resp, pl = self.list([{'start': '2020-01-01T00:00:00Z', 'duration': 7 * 365 * 86400}],
                             self.admin)
        self.assertEqual(resp.status_code, 403)
        pl.assert_not_called()

    def test_admin_avec_grant_recent_clippe_au_since(self):
        self.grant(video_since=utc(2026, 10, 4, 10, 30), email=ADMIN)
        resp, _ = self.list([{'start': '2026-10-04T09:00:00Z', 'duration': 3 * 3600}],
                            self.admin)
        self.assertEqual(resp.json(), [
            {'start': '2026-10-04T10:30:00Z', 'end': '2026-10-04T11:00:00Z',
             'duration_s': 1800, 'tracks': ['video']},
            {'start': '2026-10-04T11:00:00Z', 'end': '2026-10-04T12:00:00Z',
             'duration_s': 3600, 'tracks': ['video']},
        ])

    def test_membre_sans_grant_403(self):
        resp, pl = self.list([], self.member)
        self.assertEqual(resp.status_code, 403)
        pl.assert_not_called()

    def test_parametres_invalides(self):
        self.grant(video_since=utc(2026, 1, 1))
        self.assertEqual(self.list([], self.member, date='2026-13-01')[0].status_code, 400)
        self.assertEqual(self.list([], self.member, date='')[0].status_code, 400)
        self.assertEqual(self.list([], self.member, device='')[0].status_code, 400)
        self.assertEqual(self.list([], self.member, device='inconnu')[0].status_code, 404)

    def test_media_injoignable_503(self):
        self.grant(video_since=utc(2026, 1, 1))
        with mock.patch('api.media_client.playback_list', side_effect=MediaError('x')):
            resp = self.client.get(LIST_URL, {'device': 'webcam', 'date': '2026-10-04'},
                                   **self.member)
        self.assertEqual(resp.status_code, 503)

    def test_sans_token_refuse(self):
        # DRF répond 403 (et non 401) : KeycloakJWTAuthentication ne définit pas
        # authenticate_header — comportement commun à toute l'API, hors périmètre ici.
        with mock.patch('api.media_client.playback_list') as pl:
            resp = self.client.get(LIST_URL, {'device': 'webcam', 'date': '2026-10-04'})
        self.assertIn(resp.status_code, (401, 403))
        pl.assert_not_called()


class RecordingTicketTests(RecordingsBase):
    def test_ticket_membre(self):
        self.grant(video_since=utc(2026, 10, 4, 10), audio_since=utc(2026, 10, 4, 12))
        resp = self.ticket(self.member, start='2026-10-04T11:00:00Z', duration_s=600)
        self.assertEqual(resp.status_code, 200, resp.content)
        body = resp.json()
        self.assertEqual(body['expires_in'], 300)
        self.assertEqual(body['tracks'], ['video'])
        raw = parse_qs(urlsplit(body['url']).query)['ticket'][0]
        payload = signing.loads(raw, salt=recordings.TICKET_SALT)
        self.assertEqual(payload, {'email': MEMBER, 'device': 'webcam',
                                   'start': '2026-10-04T11:00:00Z', 'duration': 600,
                                   'tracks': ['video']})

    def test_ticket_avant_since_403(self):
        self.grant(video_since=utc(2026, 10, 4, 10))
        self.assertEqual(self.ticket(self.member, start='2026-10-04T09:59:59Z').status_code, 403)

    def test_ticket_sans_grant_403(self):
        self.assertEqual(self.ticket(self.member).status_code, 403)

    def test_duration_invalide_400(self):
        self.grant(video_since=utc(2026, 1, 1))
        for bad in (0, 3601, True, 1.5, '60', None):
            with self.subTest(duration_s=bad):
                self.assertEqual(self.ticket(self.member, duration_s=bad).status_code, 400)
        self.assertEqual(self.ticket(self.member, duration_s=3600).status_code, 200)
        self.assertEqual(self.ticket(self.member, duration_s=1).status_code, 200)

    def test_start_invalide_400_et_device_404(self):
        self.grant(video_since=utc(2026, 1, 1))
        self.assertEqual(self.ticket(self.member, start='2026-10-04T11:00:00').status_code, 400)
        self.assertEqual(self.ticket(self.member, start='hier').status_code, 400)
        self.assertEqual(self.ticket(self.member, device='inconnu').status_code, 404)

    def test_ticket_admin_sans_grant_403(self):
        self.assertEqual(self.ticket(self.admin, start='2020-01-01T00:00:00Z').status_code, 403)

    def test_ticket_admin_avant_since_403_apres_200(self):
        self.grant(video_since=utc(2026, 10, 4, 10), email=ADMIN)
        self.assertEqual(self.ticket(self.admin, start='2026-10-04T09:00:00Z').status_code, 403)
        resp = self.ticket(self.admin, start='2026-10-04T10:00:00Z')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['tracks'], ['video'])


class RecordingStreamTests(RecordingsBase):
    """Ticket + ouverture du flux. Le fMP4 source est un vrai fichier ffmpeg."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.src = cls.tmp / 'src.mp4'
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc=size=160x120:rate=10',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
            '-t', '4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-g', '10',
            '-c:a', 'libopus',
            '-f', 'mp4', '-movflags', 'empty_moov+default_base_moof+frag_keyframe',
            str(cls.src),
        ], check=True)
        cls.src_bytes = cls.src.read_bytes()
        codecs = sorted(s['codec_name'] for s in _ffprobe_streams(cls.src))
        assert codecs == ['h264', 'opus'], codecs

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        super().tearDownClass()

    def open_stream(self, path, upstream=None, **patches):
        upstream = upstream or _FakeUpstream(self.src_bytes)
        with mock.patch('api.media_client.playback_get', return_value=upstream) as pg:
            resp = self.client.get(path)
        return resp, pg, upstream

    def consume(self, resp) -> bytes:
        try:
            return b''.join(resp.streaming_content)
        finally:
            resp.close()

    def test_deux_pistes_relais_sans_ffmpeg(self):
        self.grant(video_since=utc(2026, 1, 1), audio_since=utc(2026, 1, 1))
        path = self.stream_path(self.ticket(self.member, start='2026-10-04T11:00:00Z',
                                            duration_s=600))
        with mock.patch.object(recordings.subprocess, 'Popen',
                               side_effect=AssertionError('ffmpeg ne doit pas être lancé')):
            resp, pg, up = self.open_stream(path)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp['Content-Type'], 'video/mp4')
            self.assertEqual(resp['Cache-Control'], 'no-store')
            self.assertEqual(self.consume(resp), self.src_bytes)
        pg.assert_called_once_with('webcam/av', '2026-10-04T11:00:00Z', 600)
        self.assertTrue(up.closed)

    def _remux_case(self, track, expected_codec, expected_type):
        procs = []
        real_popen = subprocess.Popen

        def spy(*a, **kw):
            p = real_popen(*a, **kw)
            procs.append((a[0], p))
            return p

        with mock.patch.object(recordings.subprocess, 'Popen', side_effect=spy):
            path = self.stream_path(self.ticket(self.member))
            resp, _, up = self.open_stream(path)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp['Content-Type'], expected_type)
            self.assertEqual(resp['Cache-Control'], 'no-store')
            out = self.tmp / f'out-{track}.mp4'
            out.write_bytes(self.consume(resp))
        streams = _ffprobe_streams(out)
        self.assertEqual([(s['codec_type'], s['codec_name']) for s in streams],
                         [(track, expected_codec)])
        (argv, proc), = procs
        self.assertIn('pipe:0', argv)
        self.assertNotIn('http', ' '.join(argv))  # aucune URL (ni identifiant) en argv
        self.assertIn('copy', argv)
        self.assertIsNotNone(proc.poll())  # ffmpeg récolté, pas de zombie
        self.assertTrue(up.closed)

    def test_video_seule_remux_reel(self):
        self.grant(video_since=utc(2026, 1, 1))
        self._remux_case('video', 'h264', 'video/mp4')

    def test_audio_seul_remux_reel(self):
        self.grant(audio_since=utc(2026, 1, 1))
        self._remux_case('audio', 'opus', 'audio/mp4')

    def test_deconnexion_client_tue_ffmpeg(self):
        self.grant(video_since=utc(2026, 1, 1))
        procs = []
        real_popen = subprocess.Popen

        def spy(*a, **kw):
            p = real_popen(*a, **kw)
            procs.append(p)
            return p

        class _Endless(_FakeUpstream):
            def iter_content(self, chunk_size):
                while not self.closed:  # amont qui ne finit jamais
                    yield from super().iter_content(chunk_size)

        with mock.patch.object(recordings.subprocess, 'Popen', side_effect=spy):
            path = self.stream_path(self.ticket(self.member))
            resp, _, up = self.open_stream(path, upstream=_Endless(self.src_bytes))
            it = iter(resp.streaming_content)
            self.assertTrue(next(it))
            resp.close()  # ce que fait Django quand le client se déconnecte
        proc, = procs
        self.assertIsNotNone(proc.poll())
        self.assertTrue(up.closed)

    def test_ticket_absent_ou_altere_403(self):
        self.grant(video_since=utc(2026, 1, 1))
        path = self.stream_path(self.ticket(self.member))
        with mock.patch('api.media_client.playback_get') as pg:
            self.assertEqual(self.client.get(STREAM_PATH).status_code, 403)
            self.assertEqual(self.client.get(path[:-3] + 'abc').status_code, 403)
            forged = signing.dumps({'email': MEMBER, 'device': 'webcam',
                                    'start': '2026-10-04T11:00:00Z', 'duration': 600,
                                    'tracks': ['video']}, salt='autre-sel')
            self.assertEqual(self.client.get(STREAM_PATH, {'ticket': forged}).status_code, 403)
        pg.assert_not_called()

    def test_ticket_expire_403(self):
        self.grant(video_since=utc(2026, 1, 1))
        with mock.patch('django.core.signing.time.time', return_value=time.time() - 301):
            path = self.stream_path(self.ticket(self.member))
        with mock.patch('api.media_client.playback_get') as pg:
            self.assertEqual(self.client.get(path).status_code, 403)
        pg.assert_not_called()

    def test_revocation_totale_403(self):
        g = self.grant(video_since=utc(2026, 1, 1))
        path = self.stream_path(self.ticket(self.member))
        g.delete()
        with mock.patch('api.media_client.playback_get') as pg:
            self.assertEqual(self.client.get(path).status_code, 403)
        pg.assert_not_called()

    def test_revocation_partielle_403(self):
        g = self.grant(video_since=utc(2026, 1, 1), audio_since=utc(2026, 1, 1))
        path = self.stream_path(self.ticket(self.member))
        g.audio, g.audio_since = False, None
        g.save()
        with mock.patch('api.media_client.playback_get') as pg:
            self.assertEqual(self.client.get(path).status_code, 403)
        pg.assert_not_called()

    def test_reoctroi_since_posterieur_403(self):
        g = self.grant(video_since=utc(2026, 1, 1))
        path = self.stream_path(self.ticket(self.member, start='2026-10-04T11:00:00Z'))
        g.video_since = utc(2026, 10, 4, 12)  # retrait puis ré-octroi plus tard
        g.save()
        with mock.patch('api.media_client.playback_get'):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_ticket_v2_admin_sans_grant_403(self):
        # Un ticket correctement signé au format v2 (« is_admin ») n'ouvre plus rien :
        # seul le Grant courant compte, sans repli Keycloak.
        raw = signing.dumps({'email': ADMIN, 'device': 'webcam',
                             'start': '2020-01-01T00:00:00Z', 'duration': 600,
                             'tracks': ['video', 'audio'], 'is_admin': True},
                            salt=recordings.TICKET_SALT)
        with mock.patch('api.media_client.playback_get') as pg:
            self.assertEqual(self.client.get(STREAM_PATH, {'ticket': raw}).status_code, 403)
        pg.assert_not_called()

    def test_admin_avec_grant_stream_comme_un_autre(self):
        self.grant(video_since=utc(2026, 1, 1), audio_since=utc(2026, 1, 1), email=ADMIN)
        path = self.stream_path(self.ticket(self.admin, start='2026-10-04T11:00:00Z'))
        resp, _, _ = self.open_stream(path)
        self.assertEqual(resp.status_code, 200)
        self.consume(resp)

    def test_media_injoignable_502(self):
        self.grant(video_since=utc(2026, 1, 1))
        path = self.stream_path(self.ticket(self.member))
        with mock.patch('api.media_client.playback_get', side_effect=MediaError('x')):
            self.assertEqual(self.client.get(path).status_code, 502)

    def test_post_refuse(self):
        self.assertEqual(self.client.post(STREAM_PATH).status_code, 405)
