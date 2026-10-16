# Feuille de route — peripheriques

App livrée le 2026-10-10, avant l'obligation de feuille de route : ses lots d'origine ne sont
pas repris ici. Ce fichier existe pour déclarer la commande de test au lanceur du lab
(`scripts/app-test.sh`), qui joue les tests avant toute mise en service.

## Commande de test
python manage.py test --settings=config.settings_test

## Notes
Les tests tournent sur SQLite en mémoire avec des réglages figés (`config/settings_test.py`).
Sans `--settings`, ils prennent le `.env` de service et échouent sur les identifiants de lab-media.

## Lot 1 — Toutes les webcams, noms d'affichage et enregistrement par webcam   ·   statut : en cours
**Objectif :** toute webcam branchée une fois sur l'hôte apparaît sans action humaine dans l'app
(page « Périphériques » des administrateurs des droits et page « Droits »), sous un nom
d'affichage modifiable, avec son interrupteur d'enregistrement ; direct et relecture par webcam.
Chantier `chantiers/webcams` (contrat v1, interfaces I0, I1, I2 servies par lab-media).
**Tâches :**
- [x] Synchronisation de `GET /v1/devices` vers `Device` (`api/inventory.py`), cache court
  (`DEVICE_SYNC_TTL_S`, 10 s), périphériques connus gardés si lab-media ne répond pas
- [x] `/api/admin/devices/` : liste, renommer (1 à 60 caractères), interrupteur d'enregistrement
  relayé, oubli d'un périphérique débranché (droits supprimés, lectures expulsées)
- [x] Hook, relecture et expulsion par les chemins donnés par I1 (jamais recomposés)
- [x] Droit Micro refusé (400) et non proposé pour une webcam sans micro
- [x] Migrations 0003/0004 : `webcam` → `webcam-ancienne`, droits et `since` gardés
- [x] Frontend : page « Périphériques », page « Droits » (vidéo seule), direct par chemin I1
- [ ] Mise en service et essai sur la vraie caméra, une fois lab-media livré (architecte)
**Tests à passer avant de clore le lot :**
- [x] `python manage.py test --settings=config.settings_test` (dont `api.tests.test_webcams`)
- [x] `npm run build`
- [ ] Après mise en service : la HD USB Camera (`usb-32e4-9230-p3-4`) apparaît, se renomme, se
  lit en direct avec un droit Vidéo ; un membre non admin reçoit 403 sur `/api/admin/devices/`
**Vérifié le :** 2026-10-17 — `essai.sh peripheriques` (PostgreSQL jetable) : 153 tests OK, dont
31 du lot (faux lab-media rejouant les réponses I1 du contrat) ; migrations 0003/0004 jouées sur
PostgreSQL jetable (avant/arrière/avant : droits et `since` identiques) ; `makemigrations --check`
sans écart ; `npm run build` vert. Non vérifié : navigateur réel, lab-media réel (pas encore livré).
