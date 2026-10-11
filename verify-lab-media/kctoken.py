"""Jeton Keycloak réel pour le client public `peripheriques`, par le flux « authorization code »
(le client n'autorise pas le password grant). Sortie : le jeton d'accès seul sur stdout.
usage : kctoken.py <base keycloak> <redirect_uri> <utilisateur>   (mot de passe dans KC_PASSWORD)
"""
import html, http.cookiejar, json, os, re, sys, urllib.parse, urllib.request, urllib.error

base, redirect, user = sys.argv[1:4]
realm = f'{base}/realms/ssolab/protocol/openid-connect'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())


def go(url, data=None):
    try:
        r = op.open(urllib.request.Request(url, data=data), timeout=20)
        return r.status, dict(r.headers), r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode('utf-8', 'replace')


q = urllib.parse.urlencode({'client_id': 'peripheriques', 'redirect_uri': redirect, 'response_type': 'code', 'scope': 'openid'})
code, hdrs, body = go(f'{realm}/auth?{q}')
for _ in range(3):
    if code in (302, 303) and 'code=' not in hdrs.get('Location', ''):
        code, hdrs, body = go(urllib.parse.urljoin(base, hdrs['Location']))
m = re.search(r'<form[^>]*action="([^"]+)"', body)
if not m:
    sys.exit(f'formulaire de connexion introuvable (HTTP {code})')
action = html.unescape(m.group(1))
# Le formulaire pointe sur l'URL publique (https://<domaine>/auth/realms/…) ; on reste sur la
# base donnée, qui est le même serveur sans le préfixe posé par Caddy.
action = action if action.startswith(base) else base + action[action.index('/realms/'):]
code, hdrs, body = go(action, urllib.parse.urlencode({'username': user, 'password': os.environ['KC_PASSWORD'], 'credentialId': ''}).encode())
loc = hdrs.get('Location', '')
if 'code=' not in loc:
    msg = re.search(r'(Access denied|Invalid username or password|kc-feedback-text">([^<]+))', body)
    sys.exit(f'pas de code d\'autorisation (HTTP {code}) : {msg.group(0) if msg else loc[:120]}')
auth_code = urllib.parse.parse_qs(urllib.parse.urlsplit(loc).query)['code'][0]
code, hdrs, body = go(f'{realm}/token', urllib.parse.urlencode({'grant_type': 'authorization_code', 'client_id': 'peripheriques', 'code': auth_code, 'redirect_uri': redirect}).encode())
tok = json.loads(body).get('access_token')
if not tok:
    sys.exit(f'échange du code refusé (HTTP {code}) : {body[:160]}')
print(tok)
