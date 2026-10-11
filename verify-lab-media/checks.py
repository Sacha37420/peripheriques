"""Contrôles de verify-lab-media.sh, lancés dans le conteneur lmt-client : il joue le
navigateur d'un utilisateur de l'app (API REST du backend avec un VRAI jeton Keycloak, HLS à
travers le Caddy de test) et, pour comparer, lit l'état brut de la brique avec les identifiants
de contrôle de l'app.

usage : checks.py <phase>   — « PASS|FAIL  <id>  <ce qui est prouvé> — <observé> » par contrôle.
"""
import base64
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

E = os.environ
API, BRICK, CADDY = 'http://lmt-periph-backend:8000', 'http://lmt-media-auth:9100', 'http://lmt-caddy'
TOKEN, CTL, HOOK_KEY = E['TOKEN'], (E['CTL_USER'], E['CTL_PASS']), E['HOOK_KEY']
STATE = '/tmp/lmt-state.json'
failed = []


def call(method, url, bearer=None, basic=None, data=None, jar=None, timeout=25):
    headers = {}
    if bearer:
        headers['Authorization'] = 'Bearer ' + bearer
    if basic:
        headers['Authorization'] = 'Basic ' + base64.b64encode(':'.join(basic).encode()).decode()
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar if jar is not None else http.cookiejar.CookieJar()))
    try:
        with op.open(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__, {}, b''


def js(method, url, **kw):
    code, _, body = call(method, url, **kw)
    try:
        return code, json.loads(body) if body else None
    except ValueError:
        return code, None


def check(cid, what, ok, observed=''):
    print(f'{"PASS" if ok else "FAIL"}  {cid:<4} {what} — {observed}', flush=True)
    if not ok:
        failed.append(cid)


def state(**kw):
    try:
        data = json.load(open(STATE))
    except (OSError, ValueError):
        data = {}
    if kw:
        data.update(kw)
        json.dump(data, open(STATE, 'w'))
    return data


def wait_for(fn, seconds=40, step=1.0):
    end = time.time() + seconds
    while True:
        value = fn()
        if value or time.time() > end:
            return value
        time.sleep(step)


def hls(kind, token, jar=None):
    return call('GET', f'{CADDY}/lab-media-hls/webcam/{kind}/index.m3u8', bearer=token, jar=jar)


def brick_sessions(protocol):
    return (js('GET', f'{BRICK}/v1/sessions?protocol={protocol}', basic=CTL)[1] or {}).get('items', [])


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse(value):
    head, _, frac = value.rstrip('Z').partition('.')
    return datetime.fromisoformat(head + ('.' + frac[:6] if frac else '') + '+00:00')


def today_paris():
    # Journée Europe/Paris du paramètre ?date= : UTC+1 ou +2 ; on interroge les deux bords.
    now = datetime.now(timezone.utc)
    return sorted({(now + timedelta(hours=h)).date().isoformat() for h in (1, 2)})


def recordings():
    out = []
    for day in today_paris():
        code, items = js('GET', f'{API}/api/recordings/?device=webcam&date={day}', bearer=TOKEN)
        if code != 200:
            return code, []
        out += [i for i in items if i not in out]
    return 200, sorted(out, key=lambda i: i['start'])


# ─────────────────────────────────────────────────────────────────────────────

def p_before_grant():
    code, me = js('GET', f'{API}/api/me/', bearer=TOKEN)
    check('A1', 'le backend de test valide le VRAI jeton Keycloak (signature, azp, groupes, email)',
          code == 200 and me['email'] == 'e2edev@ssolab.local' and me['is_admin'] is True, f'HTTP {code} {me}')
    code, _ = js('GET', f'{API}/api/me/', bearer=TOKEN[:-6] + 'AAAAAA')
    check('A2', 'jeton à la signature altérée refusé par le backend', code in (401, 403), f'HTTP {code}')
    ready = wait_for(lambda: (js('GET', f'{API}/api/grants/devices/', bearer=TOKEN)[1] or [{}])[0].get('online'), 50)
    code, devs = js('GET', f'{API}/api/grants/devices/', bearer=TOKEN)
    check('A3', 'périphérique « en ligne » : le backend le lit dans l\'API de contrôle de la brique',
          code == 200 and devs == [{'slug': 'webcam', 'label': devs[0]['label'], 'online': True}], f'HTTP {code} {devs}')
    code, devs = js('GET', f'{API}/api/devices/', bearer=TOKEN)
    check('A4', 'sans Grant, un admin ne voit aucun périphérique', code == 200 and devs == [], f'HTTP {code} {devs}')
    code, _, _ = hls('av', TOKEN)
    check('A5', 'lecture HLS REFUSÉE sans Grant (jeton pourtant valide) : la brique relaie, l\'app refuse', code == 401, f'HTTP {code}')
    code, items = recordings()
    check('A6', 'relecture refusée sans Grant', code == 403, f'HTTP {code}')


def p_after_grant():
    code, devs = js('GET', f'{API}/api/devices/', bearer=TOKEN)
    d = devs[0] if devs else {}
    check('B1', 'avec un Grant : périphérique visible, en ligne, droits et since rendus',
          code == 200 and d.get('online') is True and d['video']['allowed'] and d['audio']['allowed']
          and d['video']['since'] == state()['since'], f'HTTP {code} {devs}')
    jar = http.cookiejar.CookieJar()
    code, _, body = hls('av', TOKEN, jar)
    check('B2', 'lecture HLS AUTORISÉE avec le Grant (à travers Caddy, préfixe /lab-media-hls/)',
          code == 200 and body.startswith(b'#EXTM3U'), f'HTTP {code} {body[:16]!r}')
    sub = next((l for l in body.decode().splitlines() if l and not l.startswith('#')), '')
    state(hls_sub=sub, hls_cookies=[(c.name, c.value, c.domain, c.path) for c in jar])
    code, _, _ = hls('av', 'pas-un-jeton')
    check('B3', 'lecture HLS refusée avec un faux jeton', code == 401, f'HTTP {code}')
    code, _, _ = hls('av', None)
    check('B4', 'lecture HLS refusée sans jeton', code == 401, f'HTTP {code}')
    rtc = wait_for(lambda: [s for s in brick_sessions('webrtc') if s['state'] == 'read' and s['outboundBytes'] > 0], 40)
    check('B5', 'lecture WebRTC (WHEP par Caddy, préfixe /lab-media-webrtc/, jeton réel en Bearer) : média émis',
          bool(rtc), json.dumps(rtc))
    hl = brick_sessions('hls')
    state(sessions={'hls': [s['id'] for s in hl], 'webrtc': [s['id'] for s in rtc or []]})
    print('SESSIONS ' + json.dumps(state()['sessions']))


def p_recordings():
    since = parse(state()['since'])
    wait_for(lambda: datetime.now(timezone.utc) > since + timedelta(seconds=14), 30)
    code, raw = js('GET', f'{BRICK}/v1/recordings?path=webcam/av', basic=CTL)
    first = parse(raw[0]['start']) if raw else None
    check('R1', 'la brique a enregistré AVANT le since du droit (plage brute, identifiants de contrôle)',
          code == 200 and first is not None and first < since - timedelta(seconds=5),
          f'début brut {raw[0]["start"] if raw else None}, since {state()["since"]}')
    code, items = recordings()
    check('R2', 'liste rendue par l\'app : clippée AU since, rien d\'antérieur',
          code == 200 and items and items[0]['start'] == state()['since'] and items[0]['tracks'] == ['video', 'audio'],
          f'HTTP {code} {json.dumps(items)[:230]}')
    before = iso(since - timedelta(seconds=10))
    code, data = js('POST', f'{API}/api/recordings/ticket/', bearer=TOKEN, data={'device': 'webcam', 'start': before, 'duration_s': 8})
    check('R3', 'ticket pour un instant antérieur au since (pourtant enregistré) : refusé', code == 403, f'start {before} → HTTP {code} {data}')
    code, t = js('POST', f'{API}/api/recordings/ticket/', bearer=TOKEN, data={'device': 'webcam', 'start': state()['since'], 'duration_s': 8})
    check('R4', 'ticket au since : accordé, deux pistes', code == 200 and t['tracks'] == ['video', 'audio'], f'HTTP {code} {str(t)[:120]}')
    if code == 200:
        code, hdrs, body = call('GET', API + t['url'][len('/peripheriques-api'):])
        open('/tmp/lmt-clip-av.mp4', 'wb').write(body)
        check('R5', 'flux de la plage : fMP4 venu de la brique par l\'API de contrôle, relayé par l\'app',
              code == 200 and b'ftyp' in body[:64] and b'moof' in body and hdrs.get('Content-Type') == 'video/mp4',
              f'HTTP {code}, {len(body)} octets, {hdrs.get("Content-Type")}')


def p_revoke():
    """Révocation du droit vidéo par l'API de l'app, comme le ferait la page Droits."""
    code, grants = js('GET', f'{API}/api/grants/', bearer=TOKEN)
    gid = grants[0]['id'] if code == 200 and grants else None
    code, g = js('PATCH', f'{API}/api/grants/{gid}/', bearer=TOKEN, data={'video': False})
    check('K0', 'PATCH /api/grants/<id>/ {"video": false} par un admin', code == 200 and g['video'] is False and g['audio'] is True,
          f'HTTP {code} {json.dumps(g)[:150]}')


def p_revoked():
    """verify-lab-media.sh vient de révoquer le droit vidéo (PATCH par l'API de l'app)."""
    old = state()['sessions']
    gone = wait_for(lambda: not (set(old['hls']) & {s['id'] for s in brick_sessions('hls')})
                    and not (set(old['webrtc']) & {s['id'] for s in brick_sessions('webrtc')}), 8, 0.5)
    check('K1', 'révocation ⇒ les sessions HLS et WebRTC ouvertes sur webcam/av ont disparu de la brique',
          bool(gone) and old['hls'] and old['webrtc'], f'avant : {old} ; après : hls={[s["id"] for s in brick_sessions("hls")]} webrtc={[s["id"] for s in brick_sessions("webrtc")]}')
    jar = http.cookiejar.CookieJar()
    for name, value, domain, path in state()['hls_cookies']:
        jar.set_cookie(http.cookiejar.Cookie(0, name, value, None, False, domain, True, False, path, True, False, None, False, None, None, {}))
    code, _, _ = call('GET', f'{CADDY}/lab-media-hls/webcam/av/{state()["hls_sub"]}', jar=jar)
    check('K2', 'le lecteur HLS expulsé est coupé (sa sous-playlist, servie jusque-là sans contrôle, répond 401)', code == 401, f'HTTP {code}')
    code, _, _ = hls('av', TOKEN)
    check('K3', 'nouvelle lecture de webcam/av refusée (plus de droit vidéo)', code == 401, f'HTTP {code}')
    code, _, _ = hls('video', TOKEN)
    check('K4', 'nouvelle lecture de webcam/video refusée', code == 401, f'HTTP {code}')
    code, _, body = hls('audio', TOKEN)
    check('K5', 'le droit audio, conservé, lit toujours webcam/audio', code == 200 and body.startswith(b'#EXTM3U'), f'HTTP {code}')
    time.sleep(8)
    back = [s for s in brick_sessions('webrtc') if s['path'] == 'webcam/av' and s['state'] == 'read']
    check('K6', 'le lecteur WebRTC expulsé retente et reste dehors', back == [], f'{len(back)} session(s) WebRTC de lecture sur webcam/av')
    code, devs = js('GET', f'{API}/api/devices/', bearer=TOKEN)
    d = devs[0] if devs else {}
    check('K7', 'l\'API de l\'app reflète la révocation', code == 200 and d['video'] == {'allowed': False, 'since': None}
          and d['audio']['allowed'], json.dumps(d))
    code, items = recordings()
    check('K8', 'relecture après révocation : pistes réduites à l\'audio', code == 200 and items
          and all(i['tracks'] == ['audio'] for i in items), f'HTTP {code} {json.dumps(items)[:160]}')
    code, t = js('POST', f'{API}/api/recordings/ticket/', bearer=TOKEN, data={'device': 'webcam', 'start': state()['since'], 'duration_s': 8})
    if code == 200:
        code, hdrs, body = call('GET', API + t['url'][len('/peripheriques-api'):])
        open('/tmp/lmt-clip-audio.mp4', 'wb').write(body)
        check('K9', 'flux audio seul : la plage de la brique (deux pistes) est remuxée par l\'app',
              code == 200 and hdrs.get('Content-Type') == 'audio/mp4' and len(body) > 500, f'HTTP {code}, {len(body)} octets, {hdrs.get("Content-Type")}')
    else:
        check('K9', 'ticket audio seul', False, f'HTTP {code} {t}')


def p_hook_direct():
    body = {'ip': '10.0.0.1', 'user': E['CTL_USER'], 'password': E['CTL_PASS'], 'token': '', 'action': 'publish',
            'path': 'webcam/av', 'protocol': 'rtsp', 'id': None, 'query': '', 'userAgent': 'x'}
    url = f'{API}/api/media-auth/?key={HOOK_KEY}'
    seen = {}
    for action in ('publish', 'api', 'playback', 'metrics', 'pprof'):
        seen[action] = call('POST', url, data={**body, 'action': action})[0]
    check('H1', 'hook de l\'app, appelé avec la BONNE clé : toute action autre que read est refusée, même avec les identifiants de contrôle',
          set(seen.values()) == {401}, json.dumps(seen))
    code, _, _ = call('POST', f'{API}/api/media-auth/?key=fausse', data={**body, 'action': 'read', 'token': TOKEN, 'path': 'webcam/audio'})
    ok, _, _ = call('POST', url, data={**body, 'action': 'read', 'token': TOKEN, 'path': 'webcam/audio', 'user': '', 'password': ''})
    check('H2', 'read avec une mauvaise clé refusé ; avec la bonne clé et un droit : 200', code == 401 and ok == 200, f'mauvaise clé HTTP {code}, bonne clé HTTP {ok}')


def p_backend_down():
    """verify-lab-media.sh a ARRÊTÉ le backend de l'app."""
    before = js('GET', f'{BRICK}/v1/paths/webcam/av', basic=CTL)[1]
    code, _, _ = hls('audio', TOKEN)
    check('D1', 'backend arrêté : personne ne lit, même avec un droit et un jeton valides', code == 401, f'HTTP {code}')
    time.sleep(4)
    after = js('GET', f'{BRICK}/v1/paths/webcam/av', basic=CTL)[1]
    check('D2', 'backend arrêté : la publication continue (même session, octets reçus en hausse)',
          after['ready'] and after['readyTime'] == before['readyTime'] and after['inboundBytes'] > before['inboundBytes'],
          f'inboundBytes {before["inboundBytes"]} → {after["inboundBytes"]}, readyTime {after["readyTime"]}')


def p_backend_back():
    ok = wait_for(lambda: js('GET', f'{API}/api/me/', bearer=TOKEN)[0] == 200, 40)
    code, _, _ = hls('audio', TOKEN)
    check('D4', 'backend relancé : la lecture autorisée reprend', bool(ok) and code == 200, f'HTTP {code}')


PHASES = {name[2:]: fn for name, fn in globals().items() if name.startswith('p_')}

if __name__ == '__main__':
    PHASES[sys.argv[1]]()
    sys.exit(1 if failed else 0)
