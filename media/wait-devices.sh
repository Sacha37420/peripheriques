#!/bin/sh
# Attend que la caméra et la carte son soient (ré)apparues avant de lancer ffmpeg.
# N'OUVRE AUCUN périphérique : seulement readlink/stat et sysfs (visible dans le conteneur ;
# /proc/asound, lui, est masqué par Docker).
#
# Contexte (contrat v5, I6) : aucun `devices:` dans le compose — il fige un couple
# majeur:mineur à la création du conteneur, et une ré-énumération USB a déjà interverti
# video0/video1 en prod (ffmpeg ouvrait alors le nœud MÉTADONNÉES : « Not a tty »).
# Désormais /dev de l'hôte est monté en lecture seule sous /hostdev (vue live) et /dev/snd
# est un bind live ; l'accès est ouvert par device_cgroup_rules (81:* et 116:*).
#
# Vidéo : MEDIA_DEVICE_VIDEO (lien by-id de l'hôte, relatif ../../videoN) réécrit sous
#   /hostdev doit se résoudre vers un nœud caractère videoN dont
#   /sys/class/video4linux/videoN/index == 0 (0 = capture, 1 = métadonnées) et dont le
#   majeur:mineur == /sys/class/video4linux/videoN/dev.
# Audio : la carte nommée par CARD= de MEDIA_DEVICE_AUDIO doit exister dans
#   /sys/class/sound/cardN/id ET son nœud de capture /dev/snd/pcmC<N>D<DEV>c doit être présent.
set -u

HOSTDEV="${HOSTDEV_ROOT:-/hostdev}"
SND="${SND_DIR:-/dev/snd}"
SYSFS="${SYSFS_ROOT:-/sys}"
SETTLE="${MEDIA_DEVICE_SETTLE_S:-3}"          # délai de stabilisation après réapparition
WAIT_MAX="${MEDIA_DEVICE_WAIT_MAX_S:-0}"      # 0 = attendre indéfiniment

log() { echo "[wait-devices] $*" >&2; }

case "${MEDIA_DEVICE_VIDEO:-}" in
  /dev/*) ;;
  *) log "ERREUR : MEDIA_DEVICE_VIDEO doit être un chemin /dev/... de l'hôte (lien by-id)"; exit 2;;
esac
VIDEO_LINK="${HOSTDEV}${MEDIA_DEVICE_VIDEO#/dev}"

card="$(printf '%s' "${MEDIA_DEVICE_AUDIO:-}" | sed -n 's/^hw:.*CARD=\([^,]*\).*/\1/p')"
[ -n "$card" ] || { log "ERREUR : MEDIA_DEVICE_AUDIO doit désigner la carte par nom (hw:CARD=<nom>,DEV=n)"; exit 2; }
pcmdev="$(printf '%s' "$MEDIA_DEVICE_AUDIO" | sed -n 's/.*DEV=\([0-9][0-9]*\).*/\1/p')"
pcmdev="${pcmdev:-0}"

video_state() {
  [ -d "$HOSTDEV" ] || { echo "$HOSTDEV absent (montage /dev:/hostdev:ro manquant dans compose ?)"; return 1; }
  target="$(readlink -f "$VIDEO_LINK" 2>/dev/null)"
  [ -n "$target" ] && [ -c "$target" ] || { echo "lien $MEDIA_DEVICE_VIDEO absent ou sans nœud (caméra débranchée ?)"; return 1; }
  n="$(basename "$target")"
  sys="$SYSFS/class/video4linux/$n"
  [ -d "$sys" ] || { echo "$n sans entrée sysfs (ré-énumération en cours ?)"; return 1; }
  idx="$(cat "$sys/index" 2>/dev/null)"
  [ "$idx" = "0" ] || { echo "$MEDIA_DEVICE_VIDEO → $n, index=$idx : pas le nœud de capture"; return 1; }
  node="$(printf '%d:%d' "0x$(stat -c %t "$target")" "0x$(stat -c %T "$target")")"
  [ "$node" = "$(cat "$sys/dev" 2>/dev/null)" ] || { echo "$n incohérent (nœud $node ≠ sysfs $(cat "$sys/dev" 2>/dev/null))"; return 1; }
  return 0
}

audio_state() {
  for f in "$SYSFS"/class/sound/card*/id; do
    [ -f "$f" ] && [ "$(cat "$f" 2>/dev/null)" = "$card" ] || continue
    num="$(basename "$(dirname "$f")")"; num="${num#card}"
    pcm="$SND/pcmC${num}D${pcmdev}c"
    [ -c "$pcm" ] && return 0
    echo "carte « $card » = card$num mais $pcm absent (bind /dev/snd manquant ou ré-énumération en cours)"
    return 1
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
