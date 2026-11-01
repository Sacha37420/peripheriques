# Feuille de route — peripheriques

App livrée le 2026-10-10, avant l'obligation de feuille de route : ses lots d'origine ne sont
pas repris ici. Ce fichier existe pour déclarer la commande de test au lanceur du lab
(`scripts/app-test.sh`), qui joue les tests avant toute mise en service.

## Commande de test
python manage.py test --settings=config.settings_test

## Notes
Les tests tournent sur SQLite en mémoire avec des réglages figés (`config/settings_test.py`).
Sans `--settings`, ils prennent le `.env` de service et échouent sur les identifiants de lab-media.

## Lot 1 — Toutes les webcams, noms d'affichage et enregistrement par webcam   ·   statut : livré
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
- [x] Noms d'affichage uniques (casse et espaces de bord ignorés) : à l'arrivée « Modèle 2 »,
  « Modèle 3 »… (ordre `first_seen` puis identifiant) ; renommage vers un nom pris refusé (400) ;
  index unique `lower(trim(label))` (migrations 0005 dédoublonnage, 0006 contrainte)
- [ ] Mise en service et essai sur la vraie caméra, une fois lab-media livré (architecte)
**Tests à passer avant de clore le lot :**
- [x] `python manage.py test --settings=config.settings_test` (dont `api.tests.test_webcams`)
- [x] `npm run build`
- [ ] Après mise en service : la HD USB Camera (`usb-32e4-9230-p3-4`) apparaît, se renomme, se
  lit en direct avec un droit Vidéo ; un membre non admin reçoit 403 sur `/api/admin/devices/`
**Vérifié le :** 2026-10-17 — `essai.sh peripheriques` (PostgreSQL jetable) : 163 tests OK, dont **Mis en service le 2026-10-17** (après lab-media) : image construite pendant que l'ancienne tournait, `scripts/app-test.sh peripheriques` vert, migrations 0003 à 0006 jouées, les deux droits gardés sur « Ancienne webcam ». En navigateur réel (`e2e_member`) : les deux HD USB Camera détectées sous « HD USB Camera » et « HD USB Camera 2 », renommage vers un nom pris refusé (400), renommage libre accepté, droit Micro absent et refusé (400) sur une webcam sans micro, droit Vidéo accordé puis lecture autorisée par le hook (WebRTC et HLS), enregistrement activé → segment écrit, puis coupé. Image du direct non vue : le Chromium du runner ne décode pas le H.264.
ceux de `api.tests.test_webcams` (faux lab-media rejouant les réponses I1 du contrat, noms uniques,
courses simulées) ; migrations 0003/0004 jouées sur PostgreSQL jetable (avant/arrière/avant :
droits et `since` identiques) ; 0005/0006 sur PostgreSQL jetable : deux « HD USB Camera » →
« hd usb camera » et « HD USB Camera 2 », doublon refusé par l'index (IntegrityError) ; `makemigrations --check`
sans écart ; `npm run build` vert. Non vérifié : navigateur réel, lab-media réel (pas encore livré).

---

Lots ajoutés par l'analyse du 2026-10-31 (vue d'ensemble : `to_do_global.md` à la racine du dépôt
du lab). [C] constaté, [D] déduit.

## Lot 2 — Remise au propre de la documentation   ·   statut : à faire
**Objectif :** une feuille de route et des notes qui disent l'état réel.
**Constat [C] :**
- Lot 1 : deux cases restent `[ ]` (« Mise en service… », « Après mise en service… ») alors que le
  paragraphe « Vérifié le » relate la mise en service.
- Ce paragraphe est entrelacé : le texte est coupé au milieu.
- `chantiers/peripheriques/INTEGRATION.md` parle encore de `peripheriques-media` et de la Bbox.
**Tâches :**
- [ ] Cocher ou corriger les cases du Lot 1 et démêler son « Vérifié le »
- [ ] Archiver ou réécrire `INTEGRATION.md` (fichier d'architecte)
- [ ] `README.md` : droits par email, rôle de lab-media, page Périphériques, limites (H.264 dans le runner)
**Tests à passer avant de clore le lot :**
- [ ] Relecture ; `validate-app.sh peripheriques --quality-only` vert
**Vérifié le :**

## Lot 3 — Vérifications manuelles en conditions réelles   ·   statut : à faire
**Objectif :** Ⓟ prouver ce que le runner ne peut pas voir.
**Tâches :**
- [ ] Image du direct H.264 dans un vrai navigateur
- [ ] WebRTC depuis le WAN (port 8189 redirigé sur la Freebox)
- [ ] Révocation pendant une lecture en cours : la lecture est expulsée
- [ ] Un membre non admin des droits reçoit 403 sur `/api/admin/devices/`
- [ ] Cause des réinitialisations USB (`INTEGRATION.md` §2-3)
**Tests à passer avant de clore le lot :**
- [ ] Liste ci-dessus cochée et datée
**Vérifié le :**

## Lot 4 — Gabarit front   ·   statut : en cours
**Objectif :** même comportement que le gabarit corrigé (T3). Les polices sont déjà en chemin relatif ; le `redirectUri` est déjà sur la page courante.
**Tâches :**
- [x] `tokenBases()` (l'API de l'app seule ; WHEP/HLS posent leur en-tête eux-mêmes, hors intercepteur) ; PKCE ; `redirectUri` par `loginRedirectUri` (chantier failles, I2)
- [x] Ports liés à `${LAB_BIND_ADDR:-127.0.0.1}` (chantier failles, I1)
**Tests à passer avant de clore le lot :**
- [ ] Cloisonnement 3/3 ; lien direct conservé
**Vérifié le :** 2026-11-01 — non déployé. `ESSAI_CMD='python manage.py test --settings=config.settings_test' bash chantiers/tests/essai.sh peripheriques` : 163 tests OK ; image `peripheriques-frontend:candidat` construite ; `docker compose config` : `host_ip: 127.0.0.1`. Navigateur : après déploiement.

## Mise en service — chantier « failles » (2026-11-01)
Les mentions « non déployé » ci-dessus datent d'avant cette mise en service. Image construite pendant
que l'ancienne tournait, `scripts/app-test.sh` : 163 tests OK, puis `up -d --no-build` ; ports
publiés sur `127.0.0.1` ; client Keycloak passé en PKCE S256 exigé, URI de retour réduites à celles de
l'app (`create-app-client.sh --pkce --prune-uris`) ; cloisonnement `lab-runner` 3/3 avant et après le durcissement du client, puis de nouveau 3/3 sur Keycloak 26.8.1.
Restent les vérifications en navigateur réel notées dans les lots (images, coffres de clés, envois).
