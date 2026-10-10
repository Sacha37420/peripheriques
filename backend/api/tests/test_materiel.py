"""GET /api/admin/materiel/ (v6) et conformité du catalogue livré au format I7."""
import json
import os
import tempfile

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from .helpers import ADMIN, admin_token, bearer, make_token, patch_jwks

URL = '/api/admin/materiel/'

FAMILIES = {'A', 'B', 'C'}
CONTINUOUS = {'oui', 'non', 'seulement sur secteur', 'non confirmé'}
VERDICTS = {'recommandé', 'possible', 'déconseillé pour ce service'}
ITEM_KEYS = {'name', 'brand', 'family', 'connection', 'power', 'battery', 'night_vision',
             'tracking', 'audio', 'rtsp_or_uvc', 'continuous_24_7', 'codec', 'price_eur',
             'links', 'sources', 'caveats', 'verdict'}


def i7_errors(doc) -> list[str]:
    """Écarts au format I7 (liste vide = conforme)."""
    import datetime
    errs = []
    if not isinstance(doc, dict) or set(doc) != {'updated', 'summary', 'items'}:
        return [f'clés racine : {sorted(doc) if isinstance(doc, dict) else type(doc)}']
    try:
        datetime.date.fromisoformat(doc['updated'])
    except (TypeError, ValueError):
        errs.append(f"updated : {doc['updated']!r}")
    if not (isinstance(doc['summary'], list) and all(isinstance(p, str) for p in doc['summary'])):
        errs.append('summary : liste de str attendue')
    if not isinstance(doc['items'], list):
        return errs + ['items : liste attendue']
    s, so = (str,), (str, type(None))
    for i, it in enumerate(doc['items']):
        where = f'items[{i}]'
        if not isinstance(it, dict) or set(it) != ITEM_KEYS:
            errs.append(f'{where} clés : {sorted(set(it) ^ ITEM_KEYS) if isinstance(it, dict) else it}')
            continue
        for k in ('name', 'brand', 'connection', 'power', 'rtsp_or_uvc', 'codec'):
            if not isinstance(it[k], s):
                errs.append(f'{where}.{k} : str attendu')
        for k in ('night_vision', 'tracking', 'audio', 'price_eur'):
            if not isinstance(it[k], so):
                errs.append(f'{where}.{k} : str|null attendu')
        if not isinstance(it['battery'], bool):
            errs.append(f'{where}.battery : bool attendu')
        if it['family'] not in FAMILIES:
            errs.append(f"{where}.family : {it['family']!r}")
        if it['continuous_24_7'] not in CONTINUOUS:
            errs.append(f"{where}.continuous_24_7 : {it['continuous_24_7']!r}")
        if it['verdict'] not in VERDICTS:
            errs.append(f"{where}.verdict : {it['verdict']!r}")
        links = it['links']
        if not (isinstance(links, list) and all(
                isinstance(l, dict) and set(l) == {'label', 'url'}
                and isinstance(l['label'], str) and isinstance(l['url'], str) for l in links)):
            errs.append(f'{where}.links : [{{label, url}}] attendu')
        for k in ('sources', 'caveats'):
            if not (isinstance(it[k], list) and all(isinstance(x, str) for x in it[k])):
                errs.append(f'{where}.{k} : liste de str attendue')
    return errs


class CatalogueFileTests(SimpleTestCase):
    """Le fichier réellement livré dans l'image respecte I7."""

    def test_shipped_file_is_i7(self):
        with open(settings.MATERIEL_PATH, encoding='utf-8') as fh:
            doc = json.load(fh)
        self.assertEqual(i7_errors(doc), [])

    def test_validator_catches_errors(self):
        bad = {'updated': '2026-10-04', 'summary': ['x'], 'items': [{
            **{k: 'x' for k in ITEM_KEYS}, 'battery': 'oui', 'family': 'D',
            'continuous_24_7': 'peut-être', 'verdict': 'top', 'links': [{'url': 'u'}],
            'sources': [], 'caveats': []}]}
        errs = ' '.join(i7_errors(bad))
        for frag in ('battery', 'family', 'continuous_24_7', 'verdict', 'links'):
            self.assertIn(frag, errs)


@patch_jwks()
class MaterielEndpointTests(TestCase):
    def test_admin_gets_file_verbatim(self):
        r = self.client.get(URL, **bearer(admin_token()))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/json')
        with open(settings.MATERIEL_PATH, 'rb') as fh:
            self.assertEqual(r.content, fh.read())

    def test_reread_on_each_request(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'm.json')
            with self.settings(MATERIEL_PATH=path):
                for content in (b'{"updated": "2026-10-04", "summary": [], "items": []}',
                                b'{"updated": "2026-10-05", "summary": ["v2"], "items": []}'):
                    with open(path, 'wb') as fh:
                        fh.write(content)
                    self.assertEqual(self.client.get(URL, **bearer(admin_token())).content, content)

    def test_non_admin_403(self):
        self.assertEqual(self.client.get(URL, **bearer(make_token())).status_code, 403)

    def test_admin_cli_refused(self):
        tok = make_token(ADMIN, groups=('developers',), azp='admin-cli')
        self.assertIn(self.client.get(URL, **bearer(tok)).status_code, (401, 403))
        self.assertIn(self.client.get(URL).status_code, (401, 403))

    def test_missing_file_503(self):
        with self.settings(MATERIEL_PATH='/nonexistent/materiel.json'):
            with self.assertLogs('api.views', 'ERROR'):
                r = self.client.get(URL, **bearer(admin_token()))
        self.assertEqual((r.status_code, r.json()), (503, {'detail': 'Catalogue matériel indisponible.'}))

    def test_invalid_json_503(self):
        with tempfile.NamedTemporaryFile('wb', suffix='.json', delete=False) as fh:
            fh.write(b'{"updated": ')
        try:
            with self.settings(MATERIEL_PATH=fh.name):
                with self.assertLogs('api.views', 'ERROR'):
                    r = self.client.get(URL, **bearer(admin_token()))
        finally:
            os.unlink(fh.name)
        self.assertEqual((r.status_code, r.json()), (503, {'detail': 'Catalogue matériel indisponible.'}))
