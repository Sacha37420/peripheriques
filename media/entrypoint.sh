#!/bin/sh
# Traduit les clés I5 (peripheriques/.env, jamais renommées) en surcharges MTX_* de MediaMTX,
# puis lance MediaMTX. Toute clé manquante ou mal formée => arrêt immédiat (jamais de défaut
# silencieux sur un secret).
set -eu

die() { echo "[entrypoint] ERREUR : $*" >&2; exit 1; }

for k in MEDIA_PUBLISH_USER MEDIA_PUBLISH_PASSWORD MEDIA_HOOK_KEY MEDIA_DEVICE_AUDIO \
         MEDIA_WEBRTC_PORT MEDIA_WEBRTC_HOSTS; do
  eval "v=\${$k:-}"
  [ -n "$v" ] || die "clé $k absente ou vide dans .env"
done

# Ces valeurs sont injectées dans une URL (hook, RTSP) et dans la syntaxe du muxer tee :
# on n'accepte que [A-Za-z0-9_-] plutôt que d'échapper.
for k in MEDIA_PUBLISH_USER MEDIA_PUBLISH_PASSWORD MEDIA_HOOK_KEY; do
  eval "v=\${$k}"
  case "$v" in *[!A-Za-z0-9_-]*) die "$k contient des caractères hors [A-Za-z0-9_-]";; esac
done
case "$MEDIA_WEBRTC_PORT" in ''|*[!0-9]*) die "MEDIA_WEBRTC_PORT non numérique";; esac

# Hook d'autorisation I2 — chaque publish/read/playback/api passe par le backend.
export MTX_AUTHHTTPADDRESS="http://peripheriques-backend:8000/api/media-auth/?key=${MEDIA_HOOK_KEY}"
# ICE WebRTC (UDP + TCP sur le même port, publié tel quel sur l'hôte).
export MTX_WEBRTCLOCALUDPADDRESS=":${MEDIA_WEBRTC_PORT}"
export MTX_WEBRTCLOCALTCPADDRESS=":${MEDIA_WEBRTC_PORT}"
export MTX_WEBRTCADDITIONALHOSTS="${MEDIA_WEBRTC_HOSTS}"

exec /mediamtx /mediamtx.yml
