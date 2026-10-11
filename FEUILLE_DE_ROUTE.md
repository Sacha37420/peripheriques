# Feuille de route — peripheriques

App livrée le 2026-10-10, avant l'obligation de feuille de route : ses lots d'origine ne sont
pas repris ici. Ce fichier existe pour déclarer la commande de test au lanceur du lab
(`scripts/app-test.sh`), qui joue les tests avant toute mise en service.

## Commande de test
python manage.py test --settings=config.settings_test

## Notes
Les tests tournent sur SQLite en mémoire avec des réglages figés (`config/settings_test.py`).
Sans `--settings`, ils prennent le `.env` de service et échouent sur les identifiants de lab-media.
