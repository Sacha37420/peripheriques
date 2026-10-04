#!/bin/sh
# Attend que la caméra et la carte son soient (ré)apparues avant de lancer ffmpeg.
# N'OUVRE AUCUN périphérique : lit uniquement sysfs (visible dans le conteneur ; /proc/asound,
# lui, est masqué par Docker).
#
# Pourquoi : la webcam Jieli peut se déconnecter du bus USB et se ré-énumérer (constaté
# 2026-10-04, carte ALSA renommée « U0x12240x2a25 » et /dev/video* absent pendant 1–2 min).
# Sans cette attente, runOnInitRestart relance ffmpeg toutes les ~5 s en boucle d'erreurs.
#
# Vidéo : le nœud du conteneur (/dev/video0) a été figé à la création du conteneur avec un
# couple majeur:mineur. On attend qu'un /sys/class/video4linux/*/dev porte ce même couple.
# Si la caméra revient sous un autre mineur, le nœud et la règle cgroup du conteneur ne
# correspondent plus : seule une RECRÉATION du conteneur répare (log explicite).
# Audio : on attend /sys/class/sound/card*/id == le CARD= de MEDIA_DEVICE_AUDIO.
set -u

VIDEO_NODE="${VIDEO_NODE:-/dev/video0}"
SYSFS="${SYSFS_ROOT:-/sys}"
SETTLE="${MEDIA_DEVICE_SETTLE_S:-3}"          # délai de stabilisation après réapparition
WAIT_MAX="${MEDIA_DEVICE_WAIT_MAX_S:-0}"      # 0 = attendre indéfiniment

log() { echo "[wait-devices] $*" >&2; }

card="$(printf '%s' "${MEDIA_DEVICE_AUDIO:-}" | sed -n 's/.*CARD=\([^,]*\).*/\1/p')"
[ -n "$card" ] || { log "ERREUR : MEDIA_DEVICE_AUDIO doit désigner la carte par nom (hw:CARD=<nom>,DEV=n)"; exit 2; }

video_state() {
  [ -c "$VIDEO_NODE" ] || { echo "nœud $VIDEO_NODE absent du conteneur (devices: manquant dans compose ?)"; return 1; }
  want="$(printf '%d:%d' "0x$(stat -c %t "$VIDEO_NODE")" "0x$(stat -c %T "$VIDEO_NODE")")"
  for d in "$SYSFS"/class/video4linux/*/dev; do
    [ -f "$d" ] && [ "$(cat "$d" 2>/dev/null)" = "$want" ] && return 0
  done
  others="$(cat "$SYSFS"/class/video4linux/*/dev 2>/dev/null | tr '\n' ' ')"
  if [ -n "$others" ]; then
    echo "caméra $want absente ; présents : ${others}— si la caméra est revenue sous un autre numéro, RECRÉER le conteneur media"
  else
    echo "caméra $want absente (aucun périphérique video4linux sur l'hôte)"
  fi
  return 1
}

audio_state() {
  for f in "$SYSFS"/class/sound/card*/id; do
    [ -f "$f" ] && [ "$(cat "$f" 2>/dev/null)" = "$card" ] && return 0
  done
  echo "carte ALSA « $card » absente ; présentes : $(cat "$SYSFS"/class/sound/card*/id 2>/dev/null | tr '\n' ' ')"
  return 1
}

waited=0; last=""
while :; do
  v="$(video_state)"; vs=$?
  a="$(audio_state)"; as=$?
  if [ $vs -eq 0 ] && [ $as -eq 0 ]; then
    [ $waited -gt 0 ] && { log "périphériques revenus après ${waited}s, stabilisation ${SETTLE}s"; sleep "$SETTLE"; }
    exit 0
  fi
  msg="$( [ $vs -ne 0 ] && echo "$v" ; [ $as -ne 0 ] && echo "$a" )"
  # Log au premier constat, à chaque changement, puis toutes les 60 s.
  if [ "$msg" != "$last" ] || [ $((waited % 60)) -eq 0 ]; then
    printf '%s\n' "$msg" | while IFS= read -r l; do log "attente (${waited}s) : $l"; done
    last="$msg"
  fi
  if [ "$WAIT_MAX" -gt 0 ] && [ $waited -ge "$WAIT_MAX" ]; then
    log "abandon après ${waited}s"; exit 1
  fi
  sleep 2; waited=$((waited + 2))
done
