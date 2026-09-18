#!/usr/bin/env python3
"""A `catalog.json` összeállítása a lefutott régiók bejegyzéseiből.

A meglévő katalógust **nem dobja el**: ami most nem épült újra, az
marad, ami most készült, az felülírja a régi sorát. Így egyetlen régió
újraépítése nem viszi el a többit.

    python3 make_catalog.py <bejegyzések mappája> <catalog.json>
"""
import json
import os
import sys

BASE = 'https://github.com/tutatechno/tellway-data/releases/download/packs'


def main(entries_dir, catalog_path):
    packs = {}
    if os.path.exists(catalog_path):
        with open(catalog_path, encoding='utf-8') as f:
            for p in json.load(f).get('packs', []):
                packs[p['id']] = p

    found = 0
    for root, _dirs, files in os.walk(entries_dir):
        for name in files:
            if not name.endswith('.json'):
                continue
            with open(os.path.join(root, name), encoding='utf-8') as f:
                entry = json.load(f)
            entry['url'] = f'{BASE}/{entry["file"]}'
            packs[entry['id']] = entry
            found += 1

    # A régiók listája a kontinenssel együtt — ebből csinál a menü
    # csoportokat. Ha nincs terv, a meglévő értéket hagyjuk.
    plan_path = 'regions.json'
    if os.path.exists(plan_path):
        with open(plan_path, encoding='utf-8') as f:
            for r in json.load(f):
                if r['id'] in packs:
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
          f'összesen {total/1e9:.1f} GB')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
