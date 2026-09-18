#!/usr/bin/env python3
"""Melyik régiókból készüljön csomag — a Geofabrik saját jegyzékéből.

A szabály egyszerű és **méret szerint** dönt, nem előre beírt lista
alapján: egy ország akkor marad egyben, ha a kivonata belefér a
korlátba; ha nem, a Geofabrik saját alrégióit vesszük helyette
(Németország tartományok, az Egyesült Államok államok szerint). Így nem
kell karbantartani, mi a „nagy ország" — a Geofabrik bontása mindig
követi az adat növekedését.

Kimenet: `regions.json`, a GitHub Actions mátrixának.

    python3 plan_regions.py regions.json [max_bájt]
"""
import json
import sys
import urllib.request
import concurrent.futures

INDEX = 'https://download.geofabrik.de/index-v1-nogeom.json'

# Efölött nem egyben dolgozzuk fel az országot, hanem az alrégióit.
# 1,2 GB kivonat nagyjából 3–4 GB memóriát és fél órát jelent a
# futtatón — ez még bőven belefér, a fölötte lévők már nem.
DEFAULT_LIMIT = 1_200_000_000

# Ezekből nem készül csomag: nincs mit mesélni, vagy nem hely.
SKIP = {'antarctica'}


def size_of(url):
    try:
        req = urllib.request.Request(url, method='HEAD')
        with urllib.request.urlopen(req, timeout=60) as r:
            return int(r.headers.get('Content-Length') or 0)
    except Exception as e:  # noqa: BLE001
        print(f'  ! {url}: {e}', file=sys.stderr)
        return 0


def main(out_path, limit):
    with urllib.request.urlopen(INDEX, timeout=120) as r:
        index = json.load(r)

    feats = {}
    children = {}
    for f in index['features']:
        p = f['properties']
        feats[p['id']] = p
        parent = p.get('parent')
        if parent:
            children.setdefault(parent, []).append(p['id'])

    # A kiindulás: a kontinensek gyermekei (az országok).
    continents = [i for i, p in feats.items() if not p.get('parent')]
    queue = []
    for c in continents:
        if c in SKIP:
            continue
        queue += children.get(c, [])

    # A méreteket párhuzamosan kérdezzük meg — 250 HEAD kérés így fél perc.
    chosen = []
    while queue:
        urls = {}
        for rid in queue:
            pbf = feats[rid].get('urls', {}).get('pbf')
            if pbf:
                urls[rid] = pbf
        sizes = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            for rid, size in zip(urls, pool.map(size_of, urls.values())):
                sizes[rid] = size

        nxt = []
        for rid in queue:
            size = sizes.get(rid, 0)
            kids = children.get(rid, [])
            if size > limit and kids:
                print(f'{rid}: {size/1e9:.1f} GB → {len(kids)} alrégió')
                nxt += kids
                continue
            chosen.append({
                'id': rid,
                'name': feats[rid].get('name', rid),
                'continent': _continent(feats, rid),
                'pbf': urls.get(rid, ''),
                'pbfBytes': size,
            })
        queue = nxt

    chosen.sort(key=lambda r: r['pbfBytes'])
    total = sum(r['pbfBytes'] for r in chosen)
    print(f'{len(chosen)} régió, összesen {total/1e9:.1f} GB nyers kivonat')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(chosen, f, ensure_ascii=False, indent=1)


def _continent(feats, rid):
    cur = rid
    while feats[cur].get('parent'):
        cur = feats[cur]['parent']
    return cur


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'regions.json',
         int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_LIMIT)
