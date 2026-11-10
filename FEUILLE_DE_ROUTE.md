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
- [x] Mise en service et essai sur la vraie caméra, une fois lab-media livré (architecte) — mis en service le 2026-10-17 (voir « Vérifié le »)
**Tests à passer avant de clore le lot :**
- [x] `python manage.py test --settings=config.settings_test` (dont `api.tests.test_webcams`)
- [x] `npm run build`
- [ ] Après mise en service : la HD USB Camera (`usb-32e4-9230-p3-4`) apparaît, se renomme, se
  lit en direct avec un droit Vidéo ; un membre non admin reçoit 403 sur `/api/admin/devices/`
  — 2026-11-08 : apparition, renommage et lecture autorisée par le hook vérifiés le 2026-10-17 ;
  restent l'image du direct (H.264, vrai navigateur) et le 403 d'un non-admin, repris au lot 3
**Vérifié le :** 2026-10-17 — `essai.sh peripheriques` (PostgreSQL jetable) : 163 tests OK, dont ceux de `api.tests.test_webcams` (faux lab-media rejouant les réponses I1 du contrat, noms uniques,
courses simulées) ; migrations 0003/0004 jouées sur PostgreSQL jetable (avant/arrière/avant :
droits et `since` identiques) ; 0005/0006 sur PostgreSQL jetable : deux « HD USB Camera » →
« hd usb camera » et « HD USB Camera 2 », doublon refusé par l'index (IntegrityError) ; `makemigrations --check`
sans écart ; `npm run build` vert. Non vérifié à cette étape : navigateur réel, lab-media réel (pas encore livré) — voir la mise en service ci-dessous.
**Mis en service le 2026-10-17** (après lab-media) : image construite pendant que l'ancienne tournait, `scripts/app-test.sh peripheriques` vert, migrations 0003 à 0006 jouées, les deux droits gardés sur « Ancienne webcam ». En navigateur réel (`e2e_member`) : les deux HD USB Camera détectées sous « HD USB Camera » et « HD USB Camera 2 », renommage vers un nom pris refusé (400), renommage libre accepté, droit Micro absent et refusé (400) sur une webcam sans micro, droit Vidéo accordé puis lecture autorisée par le hook (WebRTC et HLS), enregistrement activé → segment écrit, puis coupé. Image du direct non vue : le Chromium du runner ne décode pas le H.264.
(Paragraphe démêlé le 2026-11-08 : les deux textes étaient entrelacés ; seuls ajouts : cette note, « à cette étape » et le renvoi à la mise en service.)

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
- [x] Cocher ou corriger les cases du Lot 1 et démêler son « Vérifié le » — fait le 2026-11-08 (travail de nuit)
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

## Lot 4 — Gabarit front   ·   statut : livré
**Objectif :** même comportement que le gabarit corrigé (T3). Les polices sont déjà en chemin relatif ; le `redirectUri` est déjà sur la page courante.
**Tâches :**
- [x] `tokenBases()` (l'API de l'app seule ; WHEP/HLS posent leur en-tête eux-mêmes, hors intercepteur) ; PKCE ; `redirectUri` par `loginRedirectUri` (chantier failles, I2)
- [x] Ports liés à `${LAB_BIND_ADDR:-127.0.0.1}` (chantier failles, I1)
**Tests à passer avant de clore le lot :**
- [x] Cloisonnement 3/3 ; lien direct conservé — vérifié le 2026-11-01 (lab-runner, `e2e_member`)
**Vérifié le :** 2026-11-01 — non déployé. `ESSAI_CMD='python manage.py test --settings=config.settings_test' bash chantiers/tests/essai.sh peripheriques` : 163 tests OK ; image `peripheriques-frontend:candidat` construite ; `docker compose config` : `host_ip: 127.0.0.1`. Navigateur : après déploiement.
Statut passé à « livré » le 2026-11-08 (relecture de nuit) : tâches et test cochés (navigateur 2026-11-01, commit `91db074`), mis en service le 2026-11-01 (section suivante).

## Mise en service — chantier « failles » (2026-11-01)
Les mentions « non déployé » ci-dessus datent d'avant cette mise en service. Image construite pendant
que l'ancienne tournait, `scripts/app-test.sh` : 163 tests OK, puis `up -d --no-build` ; ports
publiés sur `127.0.0.1` ; client Keycloak passé en PKCE S256 exigé, URI de retour réduites à celles de
l'app (`create-app-client.sh --pkce --prune-uris`) ; cloisonnement `lab-runner` 3/3 avant et après le durcissement du client, puis de nouveau 3/3 sur Keycloak 26.8.1.
Restent les vérifications en navigateur réel notées dans les lots (images, coffres de clés, envois).

## Lot 5 — Coupure du direct et pont « caméras d'un modèle »   ·   statut : en cours
**Objectif :** sur la page Droits, un bouton (admins des droits) coupe le direct pour tous et
arrête les enregistrements jusqu'à la réactivation ; google-agenda (dev2) montre à ceux qui sont
en lien avec un modèle les webcams auxquelles ce modèle a droit, sans être soumis à la coupure.
Chantier `chantiers/webcams-modeles` (contrat v1 : I0 coupure, I1 pont, I2 ticket `pt1.`).
**Tâches :**
- [x] Modèle `Cutoff` (ligne unique, migration 0007) ; `GET/POST /api/admin/cutoff/ {active}`
  (`api/cutoff.py`) : coupure acquise en base avant tout appel à lab-media, expulsion de toutes
  les lectures WebRTC/HLS sauf celles du pont (`kick.kick_direct`, liste prise chez lab-media),
  `record=false` sur chaque webcam enregistrée et liste gardée (`paused`), rétablissement exact
  à la levée (webcam oubliée ignorée), échecs gardés et rejouables (`failures`, `media_errors`)
- [x] Hook : refus de tout jeton Keycloak pendant la coupure ; `PATCH record=true` → 409 ;
  un réglage manuel ou un oubli retire la webcam de ce que la coupure rétablira
- [x] `POST /api/bridge/model-cameras/` (`api/bridge.py`, vue Django hors DRF) : clé
  `Authorization: Bridge <PERIPHERIQUES_BRIDGE_KEY>` en temps constant en tout premier, 404
  identiques (clé absente, fausse, vide), 405 hors POST, 400 corps invalide, forme I1 exacte
- [x] Ticket `pt1.` (signing, sel propre, 120 s) vérifié par le hook avant tout JWT : WebRTC
  seulement, droit du modèle au moment du hook, session gardée au nom du modèle marquée « pont »
  (refusée si elle ne peut pas être gardée) ; la révocation d'un droit du modèle l'expulse
- [x] Filtre de journal : `Bridge …` et `pt1.…` masqués (filet ; aucun code ne les écrit)
- [x] Frontend : carte « Direct et enregistrements » en tête de la page Droits (confirmation,
  « Coupés depuis le … », webcams suspendues, échecs non coupés / non rétablis, « Réessayer »)
- [ ] Mise en service et parcours réel (architecte) : clé posée dans `.env`, essai avec google-agenda
**Tests à passer avant de clore le lot :**
- [x] `python manage.py test --settings=config.settings_test` (dont `api.tests.test_webcams_modeles`)
- [x] `npm run build`
- [ ] Après mise en service : couper → le direct d'un membre est expulsé et refusé, les webcams
  enregistrées s'arrêtent ; le direct d'un watcher par google-agenda continue ; rétablir → les
  mêmes webcams se remettent à enregistrer
**Constat du 2026-11-08 [D] :** les conteneurs `peripheriques-*` ont été recréés le 2026-11-05 à 10 h 35,
trois minutes après le commit `d6ea9e7` (lot 6) : le code des lots 5 et 6 est vraisemblablement en
service. `scripts/app-status.sh peripheriques` garde pourtant le déploiement du 2026-11-01 (`2080f19`) :
cette mise en service n'est pas passée par le chemin qui l'enregistre. Restent les parcours réels.
**Vérifié le :** 2026-11-05 — non déployé. `essai.sh peripheriques` : 196 tests OK (33 nouveaux),
`makemigrations --check` sans écart, migration 0007 jouée avant/arrière/avant sur PostgreSQL
jetable, nouveaux tests rejoués aussi sur PostgreSQL (98 OK) ; cinq mutations des contrôles
(protocole, coupure au hook, épargne du pont, clé vide, échéance) chacune détectée par un test ;
`npm run build` vert. Non vérifié : lab-media et MediaMTX réels (le ticket dans l'en-tête WHEP),
navigateur réel, appel réel depuis google-agenda.

## Lot 6 — Haut-parleur : droit « Parler » et prise de parole en direct   ·   statut : en cours
**Objectif :** depuis la page Direct (et depuis google-agenda de dev2 pour les watchers d'un
modèle), un bouton « Parler » envoie la voix du navigateur dans le haut-parleur branché sur la
prise jack de l'hôte, si un administrateur des droits l'a accordé — comme pour les webcams.
Une seule personne parle à la fois, rien n'est enregistré, personne n'écoute.
Chantier `chantiers/haut-parleur` (contrat v1 : I1 inventaire `kind: speaker` / `paths.talk`,
I2 publication WHIP relayée au hook, I3 expulsion d'un publieur, I4 pont + `speakers`, I5 ticket).
**Tâches :**
- [x] Migration 0008 (additive) : `Device.kind`, `Device.path_talk`, `Grant.speaker`, `Grant.speaker_since`
- [x] Inventaire : objets `kind: speaker` (identifiant hors `usb-`/`webcam…`, aucun chemin de lecture,
  `paths.talk` sous `speaker/`, jamais enregistré) ; webcam portant un `talk` ou sorte inconnue ignorée ;
  `talk` déjà porté par un autre périphérique ignoré
- [x] Droits : capacité `speaker` (« Parler ») seule admise sur un haut-parleur (vidéo/micro → 400),
  refusée sur une webcam (400) ; `PATCH record` sur un haut-parleur → 409 ; « Parler » n'ouvre
  aucune relecture
- [x] Hook `publish` : chemin `talk` d'un haut-parleur connu, WebRTC seulement, id de session requis ;
  jeton Keycloak (coupure inactive + droit « Parler ») ou ticket `pt1.` (droit « Parler » du modèle,
  coupure non consultée) ; session gardée sinon refus ; `read` d'un chemin `talk` toujours refusé
- [x] Coupure : expulse aussi les publieurs WebRTC des chemins `talk` (liste de lab-media), sauf ceux
  du pont ; jamais une publication de capture. Révocation / oubli : expulsion par `kick_revoked`/`kick_paths`
- [x] Pont : clé `speakers` toujours présente (triée par nom), ticket émis si `devices` ou `speakers`
- [x] Frontend : bouton « Parler » (WHIP, jeton Keycloak, clic/clic, « Vous parlez », « Quelqu’un parle
  déjà », « Coupé », arrêt à la sortie de page) ; haut-parleur dans Droits (colonne Parler) et Périphériques
- [ ] Mise en service (après lab-media) et essai audible avec le propriétaire (architecte)
**Tests à passer avant de clore le lot :**
- [x] `python manage.py test --settings=config.settings_test` (dont `api.tests.test_haut_parleur`)
- [x] `npm run build`
- [ ] Après mise en service : un membre avec « Parler » est entendu ; sans droit / pendant la coupure,
  refusé ; un second « Parler » simultané affiche « Quelqu’un parle déjà » ; retirer le droit coupe la
  parole ; un watcher google-agenda parle pendant la coupure si le modèle a le droit
**Constat du 2026-11-08 [D] :** voir le lot 5 (conteneurs recréés le 2026-11-05 à 10 h 35, après `d6ea9e7`).
**Vérifié le :** 2026-11-05 — non déployé. `essai.sh peripheriques` : 235 tests OK (39 nouveaux,
8 assertions de forme existantes étendues des seules nouvelles clés) ; `makemigrations --check`
sans écart ; 0008 jouée avant/arrière/avant sur PostgreSQL jetable ; sept mutations des contrôles
(protocole, coupure, lecture du `talk`, publication hors `talk`, expulsion des publieurs, ticket
haut-parleur seul, « Parler » sur webcam) chacune détectée ; `npm run build` vert. Non vérifié :
lab-media et MediaMTX réels (relais `publish`, code exact du refus « déjà un publieur »), navigateur
réel (micro, WHIP), son réel.
