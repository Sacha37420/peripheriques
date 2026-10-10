#!/bin/sh
# Lancé par MediaMTX (runOnInit du chemin webcam/av, runOnInitRestart: yes).
# UN SEUL ffmpeg : ouvre caméra + micro, encode UNE fois, publie les 3 chemins I1 via le
# muxer tee (aucun ré-encodage par sortie).
#   webcam/video  H.264 seul   webcam/audio  Opus seul   webcam/av  H.264 + Opus (enregistré)
# Caméra Jieli 1224:2a25 : MJPEG 1280x720 (25 i/s imposés par le pilote) ou YUYV 640x480 max,
# aucun H.264 natif => décodage MJPEG + x264. Le passage à 15 i/s se fait par le filtre fps.
set -eu

# Attendre caméra + carte son (sysfs seulement, rien n'est ouvert) — voir wait-devices.sh.
sh /opt/peripheriques/wait-devices.sh || exit 1

# Lien by-id de l'hôte relu sous /hostdev (montage live, contrat v5) : résolu à CHAQUE
# lancement, il suit les ré-énumérations USB. Jamais de /dev/video0 figé.
VIDEO_IN="/hostdev${MEDIA_DEVICE_VIDEO#/dev}"

SLUG=webcam
BASE="rtsp://${MEDIA_PUBLISH_USER}:${MEDIA_PUBLISH_PASSWORD}@127.0.0.1:8554/${SLUG}"

exec ffmpeg -hide_banner -nostdin -loglevel warning \
  -thread_queue_size 64 \
  -f v4l2 -input_format mjpeg -video_size 1280x720 -timestamps mono2abs -i "${VIDEO_IN}" \
  -thread_queue_size 1024 \
  -f alsa -channels 1 -sample_rate 48000 -i "${MEDIA_DEVICE_AUDIO}" \
  -map 0:v:0 -map 1:a:0 \
  -vf "fps=15,format=yuv420p" \
  -c:v libx264 -preset veryfast -tune zerolatency -profile:v baseline -level:v 3.1 \
  -b:v 1000k -maxrate 1000k -bufsize 2000k -g 30 -keyint_min 30 -sc_threshold 0 -bf 0 \
  -c:a libopus -b:a 32k -ar 48000 -ac 1 -application lowdelay \
  -f tee \
  "[f=rtsp:rtsp_transport=tcp:select=v]${BASE}/video|[f=rtsp:rtsp_transport=tcp:select=a]${BASE}/audio|[f=rtsp:rtsp_transport=tcp]${BASE}/av"
