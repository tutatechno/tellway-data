#!/usr/bin/env python3
"""A `catalog.json` összeállítása a lefutott régiók bejegyzéseiből.

A meglévő katalógust **nem dobja el**: ami most nem épült újra, az
marad, ami most készült, az felülírja a régi sorát. Így egyetlen régió
újraépítése nem viszi el a többit, és egy elhasalt köteg sem.

    python3 make_catalog.py <bejegyzések mappája> <catalog.json>

A bejegyzések mappája lehet üres vagy nem létező is — ilyenkor a
katalógus változatlan marad, a futás pedig nem hibázik el.
"""
import json
import os
import sys

BASE = 'https://github.com/tutatechno/tellway-data/releases/download/packs'


def main(entries_dir, catalog_path):
    packs = {}
    if os.path.exists(catalog_path):
        with open(catalog_path, encoding='utf-8') as f:
            try:
                for p in json.load(f).get('packs', []):
                    packs[p['id']] = p
            except json.JSONDecodeError:
                print('! a régi catalog.json olvashatatlan, újat írok')

    plan = None
    found = 0
    for root, _dirs, files in os.walk(entries_dir):
        for name in files:
            if not name.endswith('.json'):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:  # noqa: BLE001
                print(f'! {path}: {e}')
                continue

            # A terv (regions.json) lista, a bejegyzés szótár.
            if name == 'regions.json' or isinstance(data, list):
                plan = data
                continue
            if not isinstance(data, dict) or 'id' not in data:
                continue

            data['url'] = f'{BASE}/{data["file"]}'
            packs[data['id']] = data
            found += 1

    # A földrész és az angol név a tervből jön — ebből csinál a menü
    # csoportokat. Ami most nem szerepel a tervben, az marad, ahogy volt.
    if plan:
        for r in plan:
            if r.get('id') in packs:
                packs[r['id']]['continent'] = r.get('continent', '')
                packs[r['id']].setdefault('name', {})['en'] = r.get(
                    'name', r['id'])

    out = {
        'format': 1,
        'packs': sorted(packs.values(),
                        key=lambda p: (p.get('continent', ''), p['id'])),
    }
    with open(catalog_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    total = sum(p.get('bytes', 0) for p in out['packs'])
    print(f'{found} új bejegyzés, {len(out["packs"])} csomag, '
          f'összesen {total / 1e9:.1f} GB')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'entries',
         sys.argv[2] if len(sys.argv) > 2 else 'catalog.json')
