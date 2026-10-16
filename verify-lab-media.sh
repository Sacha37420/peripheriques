#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# verify-lab-media.sh — prouve de bout en bout l'app peripheriques branchée sur la brique
# lab-media, SANS la caméra et SANS toucher à l'app en service.
#
#   bash deploiement/peripheriques/verify-lab-media.sh           lance tout, nettoie à la fin
#   bash deploiement/peripheriques/verify-lab-media.sh --keep    laisse la pile lmt-* en place
#   bash deploiement/peripheriques/verify-lab-media.sh --clean   supprime une pile restée en place
#
# Pile jetable, préfixe lmt- :
#   · la brique : lab-media/docker-compose.yml, projet « lmt », source = mire (capture.sh en
#     mode testsrc, le seul encodage), registre de test où `peripheriques` pointe sur…
#   · … un backend peripheriques de test : l'image de l'app déjà construite, CE code source
#     monté en lecture seule, une base PostgreSQL jetable (jamais dev_sacha) ;
#   · un Caddy de test (label détourné en « lmtcaddy ») et un lecteur WHEP (second MediaMTX).
#
# Ce qui touche au lab en service, et rien d'autre :
#   1. une connexion du compte de test e2edev à Keycloak par l'URL publique du lab, pour
#      obtenir un VRAI jeton du client `peripheriques` (flux « authorization code ») ;
#   2. une lecture des clés publiques du realm (JWKS) par la même URL. Elles sont servies à la
#      pile de test par un conteneur lmt-jwks qui porte l'alias « keycloak » : AUCUN conteneur
#      de test n'est sur sso-net ni dev-net.
# Aucun périphérique ouvert, aucun port publié hors 127.0.0.1 (ICE de test 18189).
# Durée : environ 3 minutes. Code de sortie : 0 si tout est PASS.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BRICK="$ROOT/lab-media"
ICE_PORT=18189
PY_IMG="python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d"
CADDY_IMG="lucaslorentz/caddy-docker-proxy:ci-alpine"
MTX_IMG="bluenviron/mediamtx:1.21.1-ffmpeg@sha256:00ef3d1a243f3769ca36769b47df241a0c269c83a7735005b817505c49adb651"
APP_IMG="${PERIPHERIQUES_BACKEND_IMAGE:-peripheriques-backend:latest}"
KEEP=false; FAILS=0; TOTAL=0
RUN=""

compose() { docker compose -p lmt --env-file "$RUN/test.env" -f "$BRICK/docker-compose.yml" "$@"; }

cleanup() {
  local ids
  ids="$(docker ps -aq --filter name='^lmt-')"
  [[ -n "$ids" ]] && docker rm -f -v $ids >/dev/null 2>&1
  for n in lmt-dev lmt-sso lmt_internal; do docker network rm "$n" >/dev/null 2>&1; done
  for v in lmt-recordings lmt_config; do docker volume rm "$v" >/dev/null 2>&1; done
  docker image rm lmt-auth lmt-media >/dev/null 2>&1
  [[ -n "$RUN" && -d "$RUN" ]] && rm -rf "$RUN"
  rm -rf /tmp/lmt-periph.*
}

case "${1:-}" in
  --clean) cleanup; echo "pile de test supprimée"; exit 0 ;;
  --keep) KEEP=true ;;
  '') ;;
  *) echo "usage : verify-lab-media.sh [--keep|--clean]" >&2; exit 2 ;;
esac

note()   { printf '\n\033[0;36m══ %s\033[0m\n' "$*"; }
result() { TOTAL=$((TOTAL + 1)); [[ "$1" == FAIL ]] && FAILS=$((FAILS + 1)); printf '%s  %-4s %s\n' "$1" "$2" "$3"; }
expect() { if [[ $? -eq 0 ]]; then result PASS "$1" "$2 — $3"; else result FAIL "$1" "$2 — $3"; fi; }
phase() {
  local out
  out="$(docker exec lmt-client python /v/checks.py "$1" 2>&1)"
  printf '%s\n' "$out" | grep -v '^SESSIONS '
  TOTAL=$((TOTAL + $(grep -c -E '^(PASS|FAIL) ' <<<"$out")))
  FAILS=$((FAILS + $(grep -c -E '^FAIL ' <<<"$out")))
  grep -q -E '^(PASS|FAIL) ' <<<"$out" || result FAIL "$1" "phase sans résultat (erreur du script de contrôle)"
  LAST_OUT="$out"
}
rnd() { head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
env_of() { grep -E "^$2=" "$1" 2>/dev/null | head -1 | cut -d= -f2-; }
manage() { docker exec lmt-periph-backend python manage.py shell -c "$1" 2>&1; }
rec_size() { docker exec lmt-media sh -c "du -sk /recordings/webcam/av | cut -f1"; }
authlog() { docker logs lmt-media-auth 2>&1; }

# ── 0. Préalables ────────────────────────────────────────────────────────────
if [[ -n "$(docker ps -aq --filter name='^lmt-')" || -n "$(docker network ls -q --filter name='^lmt')" ]]; then
  echo "Une pile de test lmt-* existe déjà : relancer avec --clean" >&2; exit 2
fi
docker image inspect "$APP_IMG" >/dev/null 2>&1 || { echo "Image $APP_IMG absente (image du backend de l'app)." >&2; exit 2; }
DOMAIN="$(env_of "$HERE/.env" DOMAIN)"; [[ -n "$DOMAIN" && "$DOMAIN" != CHANGE_ME ]] || DOMAIN="$(env_of "$ROOT/.env" DOMAIN)"
[[ -n "$DOMAIN" && "$DOMAIN" != CHANGE_ME ]] || { echo "DOMAIN introuvable (.env) : il faut l'URL publique de Keycloak." >&2; exit 2; }
KC="https://$DOMAIN/auth"
RUN="$(mktemp -d /tmp/lmt-periph.XXXXXX)"
$KEEP || trap cleanup EXIT

note "Jeton Keycloak réel (compte de test e2edev, client peripheriques)"
TOKEN="$(KC_PASSWORD="$(env_of "$ROOT/sso-lab/.env" E2EDEV_PASSWORD)" python3 "$HERE/verify-lab-media/kctoken.py" "$KC" "https://$DOMAIN/peripheriques/" e2edev 2>"$RUN/kc.err")"
[[ "$(tr -cd '.' <<<"$TOKEN" | wc -c)" == 2 ]]
expect T1 "jeton obtenu par le flux « authorization code », comme un navigateur" "$(python3 -c '
import sys, json, base64
t = sys.argv[1]
if t.count(".") != 2: print("échec :", open(sys.argv[2]).read().strip()[:160]); sys.exit()
p = t.split(".")[1]; c = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
print("azp=%s email=%s groupes=%s durée=%ds" % (c.get("azp"), c.get("email"), c.get("groups"), c["exp"] - c["iat"]))' "$TOKEN" "$RUN/kc.err")"
[[ "$(tr -cd '.' <<<"$TOKEN" | wc -c)" == 2 ]] || exit 1
mkdir -p "$RUN/www/realms/ssolab/protocol/openid-connect"
curl -fsS "$KC/realms/ssolab/protocol/openid-connect/certs" -o "$RUN/www/realms/ssolab/protocol/openid-connect/certs"
expect T2 "clés publiques du realm lues (JWKS), servies ensuite à la pile de test sous l'alias keycloak" "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))["keys"]), "clé(s)")' "$RUN/www/realms/ssolab/protocol/openid-connect/certs" 2>&1)"
chmod -R a+rX "$RUN/www"

CTL=$(rnd); CAP=$(rnd); HOOK=$(rnd); KEY_APP=$(rnd); DBPW=$(rnd)
cp "$HERE/verify-lab-media/registry.test.json" "$RUN/registry.json"; chmod a+r "$RUN/registry.json"
cat > "$RUN/test.env" <<ENV
LAB_MEDIA_PREFIX=lmt-media
LAB_MEDIA_SSO_NET=lmt-sso
LAB_MEDIA_DEV_NET=lmt-dev
LAB_MEDIA_RECORDINGS_VOLUME=lmt-recordings
LAB_MEDIA_CADDY_LABEL=lmtcaddy
LAB_MEDIA_ICE_BIND=127.0.0.1
LAB_MEDIA_REGISTRY=$RUN/registry.json
LAB_MEDIA_ENV_FILE=$RUN/test.env
COMPOSE_PROFILES=
DOMAIN=http://lmt-caddy
MEDIA_WEBRTC_PORT=$ICE_PORT
MEDIA_WEBRTC_IPS_FROM_INTERFACES=yes
LAB_MEDIA_HOOK_KEY=$HOOK
LAB_MEDIA_READ_HOOK_TIMEOUT_S=3
LAB_MEDIA_APP_PERIPHERIQUES_HOOK_KEY=$KEY_APP
LAB_MEDIA_APP_PERIPHERIQUES_CONTROL_USER=backend
LAB_MEDIA_APP_PERIPHERIQUES_CONTROL_PASSWORD=$CTL
LAB_MEDIA_SOURCE_CAPTURE_PUBLISH_USER=capture
LAB_MEDIA_SOURCE_CAPTURE_PUBLISH_PASSWORD=$CAP
ENV
# .env du backend de test : les clés de .env.example, valeurs jetables.
cat > "$RUN/backend.env" <<ENV
DB_HOST=lmt-db
DB_PORT=5432
DB_NAME=devdb
DB_SCHEMA=peripheriques
DB_USER=devuser
DB_PASSWORD=$DBPW
SECRET_KEY=$(rnd)$(rnd)
DEBUG=False
ALLOWED_HOSTS=*
SCRIPT_NAME=/peripheriques-api
KEYCLOAK_CLIENT_ID=peripheriques
KEYCLOAK_ISSUER_URI=http://keycloak:8080/realms/ssolab
KEYCLOAK_REQUIRED_GROUPS=developers
PERIPHERIQUES_ADMIN_GROUPS=developers
MEDIA_API_URL=http://lmt-media-auth:9100
MEDIA_INTERNAL_USER=backend
MEDIA_INTERNAL_PASSWORD=$CTL
MEDIA_HOOK_KEY=$KEY_APP
PYTHONDONTWRITEBYTECODE=1
ENV
cat > "$RUN/src.yml" <<YML
# Lecteur WHEP de test : lit webcam/av à travers le Caddy de test, jeton réel en Bearer.
logLevel: info
api: no
rtmp: no
srt: no
moq: no
hls: no
webrtc: no
rtspTransports: [tcp]
paths:
  lecteur:
    source: whep://lmt-caddy/lab-media-webrtc/webcam/av/whep
    whepBearerToken: $TOKEN
YML
chmod 600 "$RUN"/*.env "$RUN/src.yml"

# ── 1. La brique ─────────────────────────────────────────────────────────────
note "Brique lab-media de test (images sous le verrou du lab)"
bash "$ROOT/scripts/lab-lock.sh" docker compose -p lmt --env-file "$RUN/test.env" -f "$BRICK/docker-compose.yml" build -q auth media
rc=$?
[[ $rc -eq 75 ]] && { echo "Verrou du lab occupé (75) : réessayer plus tard." >&2; exit 75; }
[[ $rc -eq 0 ]] || { echo "Construction en échec ($rc)." >&2; exit 1; }
docker network create --internal lmt-dev >/dev/null
docker network create lmt-sso >/dev/null
docker volume create lmt-recordings >/dev/null

# ── 2. Le backend de l'app, sur une base jetable ─────────────────────────────
note "Backend peripheriques de test (image de l'app, ce code source, base jetable)"
docker run -d --name lmt-db --network lmt-dev -e POSTGRES_USER=devuser -e POSTGRES_PASSWORD="$DBPW" -e POSTGRES_DB=devdb postgres:16-alpine >/dev/null
docker run -d --name lmt-jwks --network lmt-dev --network-alias keycloak -v "$RUN/www:/www:ro" "$PY_IMG" python -m http.server 8080 -d /www >/dev/null
# L'image postgres redémarre une fois pendant son initialisation : pg_isready peut répondre
# juste avant. On réessaie la commande elle-même jusqu'à ce qu'elle passe.
for _ in $(seq 1 40); do
  docker exec lmt-db psql -q -U devuser -d devdb -c 'CREATE SCHEMA IF NOT EXISTS peripheriques' >/dev/null 2>&1 && break
  sleep 1
done
docker run -d --name lmt-periph-backend --network lmt-dev --env-file "$RUN/backend.env" -v "$HERE/backend:/app:ro" "$APP_IMG" >/dev/null
compose up -d --no-build auth media >/dev/null 2>&1
docker run -d --name lmt-caddy --network lmt-sso -e CADDY_INGRESS_NETWORKS=lmt-sso -e CADDY_DOCKER_LABEL_PREFIX=lmtcaddy \
  -v /var/run/docker.sock:/var/run/docker.sock:ro "$CADDY_IMG" >/dev/null
docker run -d --name lmt-client --network lmt-dev -v "$HERE/verify-lab-media:/v:ro" \
  -e TOKEN="$TOKEN" -e CTL_USER=backend -e CTL_PASS="$CTL" -e HOOK_KEY="$KEY_APP" -e PYTHONDONTWRITEBYTECODE=1 "$PY_IMG" sleep 3600 >/dev/null
docker network connect lmt-sso lmt-client
# La source : mire + bip, encodés une fois par capture.sh, publiés sur webcam/{av,video,audio}.
docker run -d --name lmt-capture --network lmt_internal --cpus 1 -e MEDIA_CAPTURE_MODE=testsrc \
  -e LAB_MEDIA_SOURCE_CAPTURE_PUBLISH_USER=capture -e LAB_MEDIA_SOURCE_CAPTURE_PUBLISH_PASSWORD="$CAP" \
  --entrypoint sh lmt-media /opt/lab-media/capture.sh >/dev/null
for _ in $(seq 1 40); do docker logs lmt-periph-backend 2>&1 | grep -q 'Starting development server' && break; sleep 1; done
if [[ "$(docker inspect -f '{{.State.Running}} {{.RestartCount}}' lmt-media 2>/dev/null)" != "true 0" \
   || "$(docker inspect -f '{{.State.Running}}' lmt-periph-backend 2>/dev/null)" != true ]]; then
  echo "pile de test non démarrée :" >&2
  docker logs --tail 5 lmt-media-auth >&2; docker logs --tail 5 lmt-media >&2; docker logs --tail 15 lmt-periph-backend >&2
  exit 1
fi
nets="$(docker ps --filter name='^lmt-' --format '{{.Names}}={{.Networks}}' | grep -c -E '(=|,)(dev-net|sso-lab_sso-net)(,|$)' || true)"
labels="$(docker ps --filter name='^lmt-' --format '{{.Labels}}' | tr ',' '\n' | grep -c -E '^caddy(\.|=|_)' || true)"
ports="$(docker ps --filter name='^lmt-' --format '{{.Ports}}' | tr ',' '\n' | grep -- '->' | grep -v '127\.0\.0\.1:' | grep -c . || true)"
[[ "$nets" == 0 && "$labels" == 0 && "$ports" == 0 ]]
expect F1 "isolement : aucun conteneur de test sur dev-net ni sso-net, aucun label « caddy », aucun port hors 127.0.0.1" "réseaux du lab : $nets, labels caddy : $labels, ports publics : $ports"
r="$(manage "
from django.db import connection
from api.models import Device
c = connection.cursor(); c.execute(\"select table_schema from information_schema.tables where table_name = 'grants'\")
print(connection.settings_dict['HOST'], connection.settings_dict['NAME'], c.fetchall()[0][0], list(Device.objects.values_list('slug', flat=True)))" | tail -1)"
[[ "$r" == "lmt-db devdb peripheriques ['webcam-ancienne']" ]]; expect F2 "backend de test sur la base jetable (migrations jouées, ancienne webcam migrée)" "$r"

# ── 3. Sans droit ────────────────────────────────────────────────────────────
note "Compte sans Grant"
phase before_grant
r="$(authlog | grep '"action":"read"' | grep -c 'hook de peripheriques : 401')"
[[ "$r" -ge 1 ]]; expect A7 "journal de la brique : la lecture a été relayée au hook de l'app, qui a refusé" "$r décision(s) « hook de peripheriques : 401 »"

# ── 4. Avec un droit ─────────────────────────────────────────────────────────
note "Grant créé dans la base jetable (vidéo + micro), 20 s après le début de l'enregistrement"
sleep 20
SINCE="$(manage "
from api.access import iso, now_s
from api.models import Grant
n = now_s()
Grant.objects.create(email='e2edev@ssolab.local', device_id='webcam-ancienne', video=True, audio=True, video_since=n, audio_since=n, granted_by='verify@ssolab.local')
print(iso(n))" | tail -1)"
docker exec lmt-client sh -c "printf '{\"since\": \"%s\"}' '$SINCE' > /tmp/lmt-state.json"
docker run -d --name lmt-src --network lmt-sso -v "$RUN/src.yml:/mediamtx.yml:ro" "$MTX_IMG" >/dev/null
phase after_grant
ids="$(grep '^SESSIONS ' <<<"$LAST_OUT" | cut -d' ' -f2-)"
rows="$(manage "import json; from api.models import MediaSession; print(json.dumps(list(MediaSession.objects.values('id', 'protocol', 'path', 'email'))))" | tail -1)"
python3 - "$ids" "$rows" <<'PY'
import json, sys
ids, rows = json.loads(sys.argv[1] or '{}'), {r['id']: r for r in json.loads(sys.argv[2])}
ok = bool(ids.get('hls')) and bool(ids.get('webrtc')) and all(
    rows.get(i, {}).get('protocol') == proto and rows[i]['email'] == 'e2edev@ssolab.local' and rows[i]['path'] == 'webcam/av'
    for proto in ('hls', 'webrtc') for i in ids[proto])
sys.exit(0 if ok else 1)
PY
expect B6 "les sessions listées par la brique (HLS et WebRTC) sont celles que le hook de l'app a enregistrées, mêmes id" "brique : $ids · app : $(python3 -c 'import json,sys; print([(r["protocol"], r["id"][:8]) for r in json.loads(sys.argv[1])])' "$rows" 2>/dev/null)"

note "Relecture, bornée par le since"
phase recordings
r="$(docker exec lmt-client cat /tmp/lmt-clip-av.mp4 2>/dev/null | docker run --rm -i --network none --entrypoint ffprobe lmt-media -v error -show_entries stream=codec_name:format=duration -of csv=p=0 pipe:0 2>&1 | tr '\n' ' ')"
[[ "$r" == *h264* && "$r" == *opus* ]]; expect R6 "la plage relue est un fMP4 lisible, deux pistes" "$r"

# ── 5. Révocation ────────────────────────────────────────────────────────────
note "Révocation du droit vidéo pendant une lecture HLS et une lecture WebRTC"
phase revoke
phase revoked
r="$(authlog | grep '"route":"sessions/kick"' | grep '"status":204' | grep -o '"protocol":"[a-z]*"' | sort | uniq -c | tr -s ' \n' ' ')"
[[ "$r" == *hls* && "$r" == *webrtc* ]]; expect K10 "journal de la brique : l'app a expulsé par l'API de contrôle, dans les deux protocoles (204)" "$r"
r="$(docker exec lmt-client cat /tmp/lmt-clip-audio.mp4 2>/dev/null | docker run --rm -i --network none --entrypoint ffprobe lmt-media -v error -show_entries stream=codec_name -of csv=p=0 pipe:0 2>&1 | tr '\n' ' ')"
[[ "$r" == "opus " ]]; expect K11 "le flux audio seul ne contient que la piste Opus" "$r"

note "Hook de l'app appelé en direct"
phase hook_direct

# ── 6. Backend de l'app arrêté ───────────────────────────────────────────────
note "Backend de l'app ARRÊTÉ"
s0="$(rec_size)"
docker stop -t 2 lmt-periph-backend >/dev/null
phase backend_down
s1="$(rec_size)"
[[ "$s1" -gt "$s0" ]]; expect D3 "backend arrêté : l'enregistrement continue" "webcam/av ${s0} ko → ${s1} ko"
docker start lmt-periph-backend >/dev/null
phase backend_back

# ── 7. Journaux ──────────────────────────────────────────────────────────────
note "Journaux"
logs="$(docker logs lmt-periph-backend 2>&1)"
leak=0; for s in "$KEY_APP" "$CTL" "$TOKEN"; do grep -q -F -- "$s" <<<"$logs" && leak=$((leak + 1)); done
[[ "$leak" == 0 && "$(grep -c 'media-auth/?key=\*\*\*' <<<"$logs")" -ge 1 ]]
expect J1 "journal du backend : la clé du hook y est masquée, ni identifiants de contrôle ni jeton" "$leak secret(s) trouvé(s), $(grep -c 'media-auth/?key=\*\*\*' <<<"$logs") appel(s) au hook journalisé(s) masqué(s)"
logs="$(authlog)"
leak=0; for s in "$KEY_APP" "$CTL" "$TOKEN" "$HOOK" "$CAP"; do grep -q -F -- "$s" <<<"$logs" && leak=$((leak + 1)); done
[[ "$leak" == 0 ]]; expect J2 "journal de lab-media-auth : aucun secret" "$leak secret(s) trouvé(s)"

echo
if [[ $FAILS -eq 0 ]]; then printf '\033[0;32m%d contrôles, tous PASS\033[0m\n' "$TOTAL"
else printf '\033[0;31m%d contrôle(s) FAIL sur %d\033[0m\n' "$FAILS" "$TOTAL"; fi
$KEEP && echo "Pile laissée en place : relancer avec --clean pour la supprimer."
[[ $FAILS -eq 0 ]]
