# Service media du chantier « peripheriques » (contrat I1/I4/I6).
# Image épinglée : MediaMTX + ffmpeg (alpine). Ne pas passer sur un tag flottant :
# les points (a)(b)(c)(d) du hook I2 ont été vérifiés en réel sur CETTE version.
FROM bluenviron/mediamtx:1.21.1-ffmpeg@sha256:00ef3d1a243f3769ca36769b47df241a0c269c83a7735005b817505c49adb651

COPY mediamtx.yml /mediamtx.yml
COPY entrypoint.sh publish.sh wait-devices.sh /opt/peripheriques/

ENTRYPOINT ["/bin/sh", "/opt/peripheriques/entrypoint.sh"]
