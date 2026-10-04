"""Relecture des enregistrements (contrat I3, consommateur de I4 playback).

Trois points d'entrée :

  * ``GET  /api/recordings/``        — morceaux relisibles d'une journée (Europe/Paris) ;
  * ``POST /api/recordings/ticket/`` — ticket signé (300 s) pour un morceau ;
  * ``GET  /api/recordings/stream/`` — flux fMP4, authentifié par le ticket seul
    (une balise ``<video src>`` ne peut pas porter de Bearer).

Le « clipping au plus tardif des since » est fait ici, côté serveur : la liste
découpe chaque plage MediaMTX aux frontières d'heure pleine UTC **et** à chaque
``since`` des droits, de sorte que chaque morceau ait un ensemble de pistes
constant (``tracks_at`` évalué à son début). Le ticket puis le flux ré-appliquent
``tracks_at`` : rien n'est délégué au frontend.

Les pistes non autorisées sont retirées par remux ``ffmpeg -c copy`` (jamais de
ré-encodage — 2 vCPU). ffmpeg lit le fMP4 sur **stdin** : l'URL de playback
porte les identifiants internes et ne doit jamais apparaître dans ``ps``.
"""
from __future__ import annotations

import logging
import math
import re
import subprocess
import threading
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.http import HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import media_client
from .access import has_any, iso, parse_iso, rights_for, tracks_at
from .models import Device

log = logging.getLogger(__name__)

TICKET_SALT = 'peripheriques.recording-ticket'
MAX_DURATION_S = 3600
CHUNK = 64 * 1024
_HOUR = timedelta(hours=1)
_FRACTION = re.compile(r'(\.\d{6})\d+')


def _av_path(slug: str) -> str:
    return f'{slug}/av'


def _parse_mtx_start(value) -> datetime:
    """Horodatage de MediaMTX ``/list`` : RFC 3339, parfois à la nanoseconde,
    que ``fromisoformat`` refuse au-delà de la microseconde."""
    if isinstance(value, str):
        value = _FRACTION.sub(r'\1', value)
    return parse_iso(value)


def _next_hour(dt: datetime) -> datetime:
    """Première heure pleine UTC strictement postérieure à ``dt``."""
    return dt.replace(minute=0, second=0, microsecond=0) + _HOUR


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """[00:00, 00:00 du lendemain) dans RECORDINGS_TZ, en UTC — 23 h ou 25 h
    les jours de changement d'heure (minuit n'est jamais ambigu à Paris)."""
    tz = ZoneInfo(settings.RECORDINGS_TZ)
    start = datetime.combine(day, time(0), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _pieces(start: datetime, end: datetime, cuts: list[datetime]):
    """Découpe [start, end) aux heures pleines UTC et aux instants ``cuts``."""
    inner = sorted({c for c in cuts if start < c < end})
    cur = start
    while cur < end:
        nxt = min(_next_hour(cur), end)
        while inner and inner[0] <= cur:
            inner.pop(0)
        if inner and inner[0] < nxt:
            nxt = inner.pop(0)
        yield cur, nxt
        cur = nxt


def _device_or_none(slug) -> Device | None:
    if not isinstance(slug, str) or not slug:
        return None
    return Device.objects.filter(slug=slug).first()


class RecordingListView(APIView):
    """GET /api/recordings/?device=<slug>&date=YYYY-MM-DD"""

    def get(self, request):
        slug = request.query_params.get('device')
        raw_date = request.query_params.get('date')
        if not slug:
            return Response({'device': ['Paramètre requis.']}, status=status.HTTP_400_BAD_REQUEST)
        try:
            day = date.fromisoformat(raw_date or '')
        except ValueError:
            return Response({'date': ['Format attendu : YYYY-MM-DD.']},
                            status=status.HTTP_400_BAD_REQUEST)
        device = _device_or_none(slug)
        if device is None:
            return Response({'detail': 'Périphérique inconnu.'}, status=status.HTTP_404_NOT_FOUND)

        rights = rights_for(request.user.email, device)
        if not has_any(rights):
            return Response({'detail': 'Aucun droit sur ce périphérique.'},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            ranges = media_client.playback_list(_av_path(device.slug))
        except media_client.MediaError as exc:
            log.warning('recordings list %s : %s', device.slug, exc)
            return Response({'detail': 'Serveur média indisponible.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        day_start, day_end = _day_bounds(day)
        cuts = [r.since for r in rights.values() if r.allowed and r.since is not None]
        out = []
        for item in ranges:
            try:
                r_start = _parse_mtx_start(item.get('start'))
                r_end = r_start + timedelta(seconds=float(item.get('duration') or 0))
            except (AttributeError, TypeError, ValueError):
                log.warning('recordings list %s : plage illisible %r', device.slug, item)
                continue
            start, end = max(r_start, day_start), min(r_end, day_end)
            for p_start, p_end in _pieces(start, end, cuts):
                tracks = tracks_at(rights, p_start)
                if not tracks:
                    continue
                out.append({
                    'start': iso(p_start),
                    'end': iso(p_end),
                    'duration_s': math.ceil((p_end - p_start).total_seconds()),
                    'tracks': tracks,
                })
        out.sort(key=lambda s: parse_iso(s['start']))
        return Response(out)



class RecordingTicketView(APIView):
    """POST /api/recordings/ticket/  {device, start, duration_s}"""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        duration = data.get('duration_s')
        if (not isinstance(duration, int) or isinstance(duration, bool)
                or not 1 <= duration <= MAX_DURATION_S):
            return Response({'duration_s': [f'Entier de 1 à {MAX_DURATION_S} attendu.']},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            start = parse_iso(data.get('start'))
        except ValueError as exc:
            return Response({'start': [f'Horodatage ISO 8601 avec fuseau attendu ({exc}).']},
                            status=status.HTTP_400_BAD_REQUEST)
        device = _device_or_none(data.get('device'))
        if device is None:
            return Response({'detail': 'Périphérique inconnu.'}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        tracks = tracks_at(rights_for(user.email, device), start)
        if not tracks:
            return Response({'detail': 'Aucune piste relisible à cet instant.'},
                            status=status.HTTP_403_FORBIDDEN)

        ticket = signing.dumps({
            'email': user.email,
            'device': device.slug,
            'start': iso(start),
            'duration': duration,
            'tracks': tracks,
        }, salt=TICKET_SALT)
        prefix = settings.FORCE_SCRIPT_NAME or ''
        return Response({
            'url': f'{prefix}/api/recordings/stream/?ticket={quote(ticket, safe="")}',
            'expires_in': settings.RECORDING_TICKET_MAX_AGE,
            'tracks': tracks,
        })


# ── Flux ─────────────────────────────────────────────────────────────────────

def _forbidden() -> HttpResponse:
    return HttpResponse(status=403)


def _ticket_still_valid(payload: dict) -> bool:
    """Re-vérification des droits (Grant seul, contrat v3) à l'ouverture du flux.

    Toute révocation, même partielle, d'une piste du ticket ⇒ refus (le
    frontend redemande un ticket). Être admin n'y change rien."""
    wanted = set(payload['tracks'])
    if not wanted:
        return False
    start = parse_iso(payload['start'])
    current = set(tracks_at(rights_for(payload['email'], payload['device']), start))
    return wanted <= current


class _Relay:
    """Relais direct du fMP4 amont (deux pistes). ``close()`` est appelé par
    Django en fin de réponse, y compris si le client s'est déconnecté."""

    def __init__(self, upstream):
        self.upstream = upstream

    def __iter__(self):
        for chunk in self.upstream.iter_content(CHUNK):
            if chunk:
                yield chunk

    def close(self):
        self.upstream.close()


class _Remux:
    """Remux ``ffmpeg -c copy`` vers une seule piste, en flux.

    Un thread pompe ``upstream.iter_content`` vers le stdin de ffmpeg ; le
    générateur relit stdout. ``close()`` tue ffmpeg s'il tourne encore, ferme
    la réponse amont, joint le thread et récolte le processus (pas de zombie).
    ffmpeg n'est lancé qu'à la première itération."""

    def __init__(self, upstream, track: str):
        self.upstream = upstream
        self.track = track
        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self._closed = False

    def _cmd(self) -> list[str]:
        stream = '0:v:0' if self.track == 'video' else '0:a:0'
        return [
            'ffmpeg', '-hide_banner', '-nostdin', '-loglevel', 'error',
            '-i', 'pipe:0',
            '-map', stream, '-c', 'copy',
            '-f', 'mp4', '-movflags', 'empty_moov+default_base_moof+frag_keyframe',
            '-frag_duration', '1000000',
            'pipe:1',
        ]

    def _pump(self) -> None:
        proc = self.proc
        try:
            for chunk in self.upstream.iter_content(CHUNK):
                if self._closed:
                    break
                if chunk:
                    proc.stdin.write(chunk)
        except Exception as exc:  # amont coupé, ffmpeg mort (BrokenPipe), fermeture…
            if not self._closed:
                log.info('recordings remux : pompe interrompue (%s)', exc)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    def __iter__(self):
        if self._closed:
            return
        self.proc = subprocess.Popen(
            self._cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, close_fds=True,
        )
        self.thread = threading.Thread(target=self._pump, name='recording-remux-pump',
                                       daemon=True)
        self.thread.start()
        while True:
            data = self.proc.stdout.read1(CHUNK)
            if not data:
                break
            yield data
        rc = self.proc.wait()
        if rc != 0:
            log.warning('recordings remux : ffmpeg a terminé avec le code %s', rc)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        try:
            self.upstream.close()
        except Exception:
            pass
        if self.thread is not None:
            self.thread.join(timeout=10)
        if proc is not None:
            proc.wait()
            for f in (proc.stdout, proc.stdin):
                try:
                    if f:
                        f.close()
                except Exception:
                    pass


@require_GET
def recording_stream(request):
    """GET /api/recordings/stream/?ticket=… — vue Django simple, sans Bearer."""
    raw = request.GET.get('ticket')
    if not raw:
        return _forbidden()
    try:
        payload = signing.loads(raw, salt=TICKET_SALT,
                                max_age=settings.RECORDING_TICKET_MAX_AGE)
        start = parse_iso(payload['start'])
        duration = payload['duration']
        tracks = list(payload['tracks'])
        slug = payload['device']
        if not isinstance(duration, int) or not isinstance(slug, str):
            return _forbidden()
        if not _ticket_still_valid(payload):
            return _forbidden()
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return _forbidden()

    try:
        upstream = media_client.playback_get(_av_path(slug), iso(start), duration)
    except media_client.MediaError as exc:
        log.warning('recordings stream %s : %s', slug, exc)
        return HttpResponse(status=502)

    if set(tracks) >= {'video', 'audio'}:
        body, ctype = _Relay(upstream), 'video/mp4'
    else:
        track = tracks[0]
        body = _Remux(upstream, track)
        ctype = 'video/mp4' if track == 'video' else 'audio/mp4'
    resp = StreamingHttpResponse(body, content_type=ctype)
    resp['Cache-Control'] = 'no-store'
    return resp
