# peripheriques / media — MediaMTX

C'est le seul conteneur qui ouvre la webcam et le micro de l'hôte. Il diffuse le direct (WHEP et HLS) et enregistre en continu. Il ne prend **aucune** décision d'accès : chaque publication, lecture, relecture ou appel d'API passe par le hook du backend (contrat `chantiers/peripheriques/CONTRAT.md`, interfaces I1, I2, I4, I6).

## Architecture

```
caméra (/hostdev/v4l/by-id/…index0, MJPEG 720p) ─┐
micro ALSA U20 (mono 48 k) ──┴─ ffmpeg (publish.sh, UN encodage : x264 + Opus)
                                  └─ tee → RTSP local → webcam/video · webcam/audio · webcam/av
MediaMTX ── webcam/av enregistré : /recordings/webcam/av/*.mp4 (fMP4, 1 h, purge 7 j)
         ── WHEP :8889 et HLS :8888 (derrière Caddy) · ICE :8189 UDP+TCP (publié sur l'hôte)
         ── API :9997 et playback :9996 (réseau Docker seulement, identifiants internes)
         ── authMethod: http → POST peripheriques-backend:8000/api/media-auth/?key=…
```

| Fichier | Rôle |
|---|---|
| `Dockerfile` | Image épinglée par digest : `bluenviron/mediamtx:1.21.1-ffmpeg`. Ne pas passer sur un tag flottant : le comportement du hook a été vérifié sur cette version précise. |
| `mediamtx.yml` | Configuration fixe. Seuls trois chemins existent (`webcam/{av,video,audio}`) ; tout autre chemin est refusé. RTMP, SRT, MoQ, metrics et pprof sont coupés, RTSP est en TCP seul. |
| `entrypoint.sh` | Traduit les clés `.env` I5 en surcharges `MTX_*` (adresse du hook, ICE, hôtes annoncés). Il s'arrête si une clé manque ou contient autre chose que `[A-Za-z0-9_-]`. |
| `publish.sh` | L'unique ffmpeg, lancé par `runOnInit` avec `runOnInitRestart: yes`. |
| `wait-devices.sh` | Avant ffmpeg, attend que le lien by-id pointe vers un nœud de **capture** et que le PCM de capture de la carte nommée soit présent. Il lit seulement sysfs et n'ouvre aucun périphérique. |

## Pièges, tous constatés sur 1.21.1

- **HLS n'appelle le hook qu'à l'ouverture de la session**, c'est-à-dire sur la playlist multivariant. Ensuite, sous-playlists et segments sont servis grâce au seul secret de session (cookie `hlsSession`, ou `?session=` en HTTP), sans `Authorization` et sans hook. Une révocation ne coupe donc le HLS que par `POST /v3/hlssessions/kick/<id>`. Pour WebRTC, c'est `webrtcsessions/kick`.
- **L'uuid de l'en-tête `Location` d'une réponse WHEP n'est pas l'id de session.** L'id à utiliser pour le kick est celui que reçoit le hook. C'est aussi celui de `/v3/webrtcsessions/list` et de `/v3/hlssessions/list`.
- **La purge `recordDeleteAfter` se compte depuis le début du segment**, pas depuis sa fin. Un segment d'une heure disparaît 7 jours après sa première seconde.
- **En RTSP, le hook reçoit le mot de passe aussi dans `token`.** C'est sans conséquence, puisque la règle `publish` compare `user`/`password`.
- **Interdit ici : `devices:` dans le compose (contrat v5).** Un `devices:` fige un couple majeur:mineur au moment de la création du conteneur.
  - Incident constaté en prod : une ré-énumération USB a interverti `video0` et `video1` sur l'hôte. Le conteneur ouvrait alors le nœud de **métadonnées** de la caméra, et ffmpeg échouait en boucle (`VIDIOC_G_INPUT: Not a tty`, code 231). Seule une recréation du conteneur l'a réparé.
  - Montage qui remplace `devices:` :
    - `/dev:/hostdev:ro` : une vue live de `/dev` de l'hôte. Le lien by-id, relatif `../../videoN`, s'y résout à **chaque** lancement de `publish.sh`, qui ouvre `/hostdev${MEDIA_DEVICE_VIDEO#/dev}`.
    - `/dev/snd:/dev/snd` : ALSA ne cherche que dans `/dev/snd`, et ce bind est vu en direct.
    - `device_cgroup_rules: ["c 81:* rmw", "c 116:* rmw"]` : autorise tous les numéros mineurs de video4linux (81) et d'ALSA (116), et rien d'autre.
  - ⚠ Le `:ro` n'empêche **pas** d'écrire dans un nœud caractère : c'est vérifié sur `/hostdev/null`, et le montage n'a pas `nodev`. Seules les `device_cgroup_rules` limitent les périphériques que le conteneur peut ouvrir. Ne jamais les élargir (en particulier pas de `b *:*`, ni de `privileged`).
- **Nommage des périphériques.** `MEDIA_DEVICE_AUDIO` désigne la carte par son nom (`hw:CARD=U20,DEV=0`), jamais par son numéro. `MEDIA_DEVICE_VIDEO` est le lien `by-id` de l'hôte : udev le fait pointer vers le nœud dont l'index vaut 0, c'est-à-dire le nœud de capture.
  - La webcam Jieli `1224:2a25` se déconnecte du bus USB sous débit soutenu, probablement à cause de l'alimentation ou du port : préférer un port arrière ou un hub alimenté.
  - Elle est déjà revenue dégradée : carte ALSA `U0x12240x2a25` et plus aucun `/dev/video*` pendant une à deux minutes.
  - `wait-devices.sh` attend que tout soit revenu, en lisant seulement `readlink`, `stat` et sysfs, sans rien ouvrir. Il vérifie que :
    - le lien se résout vers un `videoN` dont `/sys/class/video4linux/videoN/index` vaut `0`, et dont le couple majeur:mineur est cohérent avec sysfs ;
    - la carte nommée existe dans `/sys/class/sound/card*/id`, et son nœud de capture `/dev/snd/pcmC<N>D<DEV>c` est présent.
  - Il logge au premier constat, à chaque changement, puis toutes les 60 s. Il n'y a plus de cas « recréer le conteneur ».
- **Formats de la caméra** : MJPEG en 1280×720, 640×480 ou 640×320, et YUYV en 640×480 au maximum. Il n'y a pas de H.264 natif, et le pilote impose 25 i/s. D'où le décodage MJPEG, le filtre `fps=15` puis l'encodage x264 (baseline 3.1, CBR 1 Mbit/s, GOP de 2 s). Le profil baseline est retenu pour la compatibilité WebRTC.
- **Docker masque `/proc/asound` dans les conteneurs.** Pour savoir quelles cartes son sont présentes, lire `/sys/class/sound/card*/id`.

## Tester sans déployer

Lancer un conteneur jetable `peripheriques-media-test` sur un réseau Docker temporaire, avec un stub HTTP qui porte l'alias `peripheriques-backend` et répond 200 ou 401. Pour ne pas toucher à la caméra, remplacer `publish.sh` par une source `lavfi` (`testsrc2` et `sine`). Pour tester la purge, réduire `recordSegmentDuration` et `recordDeleteAfter` dans une **copie** de la configuration.
