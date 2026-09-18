#!/usr/bin/env python3
"""TellWay — egy régió `.twpack` csomagja, egy menetben.

Használat:

    python3 build_region.py <regio.osm.pbf> <kimeneti_mappa> <id> <angol név> <kivonat dátuma>

Ez a változat **GitHub Actions futtatóra** készült: nincs benne
szeletelés (ott nincs három perces korlát), és a köztes adatot SQLite-ba
írja, nem a memóriába — így egy nagyobb ország sem feszíti szét a
futtatót. A kimenet bájtra ugyanolyan felépítésű, mint a kézi
`measure.py` + `build_pack.py` páros kimenete.

## A csomag (`.twpack`, 1-es formátum)

    b"TWPK" | u8 formátum=1 | u32 BE fejléchossz | gzip(fejléc JSON) | csempék

- **rec** (0,05°): minden elem, aminek van neve, Wikidata- vagy
  Wikipédia-azonosítója; teljes címkekészlettel és hellyel. Ami több
  csempébe lóg, mindegyikben benne van; a nagyon nagyok (Balaton,
  országhatár) a külön **large** listában.
- **tag** (0,05°-os tömb, benne 0,01°-os cellák): `s` = névtelen, de
  megkülönböztető címkés elemek címkekészletei darabszámmal, `h` = a
  maradék elsődleges kulcs=érték darabszámai.

Licenc: a csomag az OSM-ből származtatott adatbázis → ODbL 1.0.
"""
import sys
import os
import json
import gzip
import math
import struct
import sqlite3
import hashlib
import time
import collections

import osmium

REC = 0.05
TAG = 0.01
LARGE_TILES = 16

PRIMARY = ['aerialway', 'aeroway', 'amenity', 'barrier', 'boundary', 'building',
           'craft', 'emergency', 'geological', 'healthcare', 'highway',
           'historic', 'landuse', 'leisure', 'man_made', 'military', 'natural',
           'office', 'place', 'power', 'public_transport', 'railway', 'route',
           'shop', 'sport', 'telecom', 'tourism', 'water', 'waterway']
PSET = set(PRIMARY)

# Kulcsok, amik semmit nem mondanak el a helyről (felolvasás szempontjából).
BORING_PREFIX = ('addr:', 'source', 'note', 'fixme', 'FIXME', 'created_by',
                 'check_date', 'survey', 'roof:', 'building:', 'turn:',
                 'destination', 'parking:', 'cycleway', 'sidewalk', 'lanes',
                 'maxspeed', 'oneway', 'surface', 'smoothness', 'tracktype',
                 'width', 'layer', 'level', 'lit', 'area', 'ref:', 'hgv',
                 'motor', 'bicycle', 'foot', 'horse', 'access', 'zone:',
                 'traffic', 'bus', 'junction', 'bridge:', 'tunnel:', 'is_in',
                 'postal_code', 'maxweight', 'maxheight', 'crossing', 'kerb',
                 'tactile', 'highway:', 'lcn', 'lwn', 'rcn', 'nat_', 'old_ref',
                 'int_ref', 'step_count', 'incline', 'handrail', 'service',
                 'sac_scale', 'trail_visibility', 'mtb', 'osmc:', 'colour',
                 'wheelchair', 'height', 'min_height', 'entrance', 'power:',
                 'voltage', 'frequency', 'cables', 'wires', 'line', 'circuits',
                 'location', 'operator:', 'design', 'structure', 'material',
                 'type')
DROP_IN_RECORD = ('source', 'note', 'fixme', 'FIXME', 'created_by',
                  'check_date')


def fl(x, step):
    # Lebegőpontos kerekítés ellen: 47.65 / 0.05 ne legyen 952,9999.
    return math.floor(x / step + 1e-9)


def rec_json(kind, oid, lat, lon, tags, bbox=None):
    d = {'type': kind, 'id': oid}
    if kind == 'node':
        d['lat'] = round(lat, 6)
        d['lon'] = round(lon, 6)
    else:
        d['center'] = {'lat': round(lat, 6), 'lon': round(lon, 6)}
        d['bounds'] = [round(x, 6) for x in bbox]
    d['tags'] = {k: v for k, v in tags.items()
                 if not k.startswith(DROP_IN_RECORD)}
    return json.dumps(d, ensure_ascii=False, separators=(',', ':'))


class Packer:
    def __init__(self, db_path):
        if os.path.exists(db_path):
            os.remove(db_path)
        self.db = sqlite3.connect(db_path)
        self.db.execute('PRAGMA journal_mode=OFF')
        self.db.execute('PRAGMA synchronous=OFF')
        self.db.execute('PRAGMA cache_size=-200000')  # ~200 MB
        self.db.execute('CREATE TABLE rec(i INT, j INT, line TEXT)')
        self.db.execute('CREATE TABLE large(line TEXT)')
        self.db.execute('CREATE TABLE sets(i INT, j INT, tags TEXT)')
        self.db.execute('CREATE TABLE hist(i INT, j INT, kv TEXT)')
        self.stats = collections.Counter()
        self.bbox = [90.0, 180.0, -90.0, -180.0]
        self._rec = []
        self._sets = []
        self._hist = []
        self._large = []

    # ------------------------------------------------------------- írás
    def flush(self, force=False):
        if force or len(self._rec) > 200000:
            self.db.executemany('INSERT INTO rec VALUES(?,?,?)', self._rec)
            self._rec.clear()
        if force or len(self._sets) > 200000:
            self.db.executemany('INSERT INTO sets VALUES(?,?,?)', self._sets)
            self._sets.clear()
        if force or len(self._hist) > 400000:
            self.db.executemany('INSERT INTO hist VALUES(?,?,?)', self._hist)
            self._hist.clear()
        if force or len(self._large) > 5000:
            self.db.executemany('INSERT INTO large VALUES(?)',
                                [(x,) for x in self._large])
            self._large.clear()

    def handle(self, kind, oid, tags, lat, lon, bbox=None):
        tags = dict(tags)
        named = 'name' in tags or 'wikidata' in tags or 'wikipedia' in tags

        if named:
            b = self.bbox
            b[0] = min(b[0], lat)
            b[1] = min(b[1], lon)
            b[2] = max(b[2], lat)
            b[3] = max(b[3], lon)
            line = rec_json(kind, oid, lat, lon, tags, bbox)
            self.stats['records'] += 1
            if bbox is not None:
                i0, i1 = fl(bbox[0], REC), fl(bbox[2], REC)
                j0, j1 = fl(bbox[1], REC), fl(bbox[3], REC)
                span = (i1 - i0 + 1) * (j1 - j0 + 1)
                if span > LARGE_TILES:
                    self._large.append(line)
                    self.stats['large'] += 1
                else:
                    for i in range(i0, i1 + 1):
                        for j in range(j0, j1 + 1):
                            self._rec.append((i, j, line))
            else:
                self._rec.append((fl(lat, REC), fl(lon, REC), line))
            self.flush()
            return

        has_primary = False
        for k in tags:
            if k in PSET:
                has_primary = True
                break
        if not has_primary:
            self.stats['ignored'] += 1
            return

        i, j = fl(lat, TAG), fl(lon, TAG)
        quals = [k for k in tags
                 if k not in PSET and not k.startswith(BORING_PREFIX)]
        if quals:
            # Megkülönböztető címkés, névtelen elem: a teljes címkekészlet
            # kell (elektromos kerítés, fafajta), de hely nélkül.
            self._sets.append((i, j, json.dumps(
                {k: v for k, v in tags.items()
                 if not k.startswith(DROP_IN_RECORD)},
                sort_keys=True, ensure_ascii=False, separators=(',', ':'))))
            self.stats['sets'] += 1
        else:
            for k, v in tags.items():
                if k in PSET:
                    self._hist.append((i, j, k + '=' + v))
            self.stats['hist'] += 1
        self.flush()


def build(pbf, out_dir, region_id, name_en, extract_date, db_path):
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    p = Packer(db_path)

    # 0. menet: mely utak és pontok kellenek a relációk dobozához.
    need_way, need_node = set(), set()
    for r in osmium.FileProcessor(pbf, osmium.osm.RELATION):
        if not r.tags:
            continue
        for m in r.members:
            if m.type == 'w':
                need_way.add(m.ref)
            elif m.type == 'n':
                need_node.add(m.ref)
    print(f'0. menet: {len(need_way)} tagút, {time.time()-t0:.0f}s', flush=True)

    # 1. menet: pontok és utak. A címkétlen pontokat a helyfeloldás után
    # C++-ban dobjuk el — Pythonban tízszer ennyi ideig tartana.
    geo = {}
    fp = (osmium.FileProcessor(pbf, osmium.osm.NODE | osmium.osm.WAY)
          .with_locations()
          .with_filter(osmium.filter.EmptyTagFilter()
                       .enable_for(osmium.osm.NODE)))
    for o in fp:
        if o.is_node():
            la, lo = o.location.lat, o.location.lon
            if o.id in need_node:
                geo[('n', o.id)] = (la, lo, la, lo)
            p.handle('node', o.id, o.tags, la, lo)
        else:
            tagged = len(o.tags) > 0
            if not tagged and o.id not in need_way:
                continue
            la, lo = [], []
            for nd in o.nodes:
                loc = nd.location
                if loc.valid():
                    la.append(loc.lat)
                    lo.append(loc.lon)
            if not la:
                continue
            bb = (min(la), min(lo), max(la), max(lo))
            if o.id in need_way:
                geo[('w', o.id)] = bb
            if tagged:
                p.handle('way', o.id, o.tags,
                         (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2, bb)
    print(f'1. menet: {time.time()-t0:.0f}s, {dict(p.stats)}', flush=True)

    # 2. menet: relációk, a tagjaik dobozából.
    for r in osmium.FileProcessor(pbf, osmium.osm.RELATION):
        if not r.tags:
            continue
        boxes = [geo[(m.type, m.ref)] for m in r.members
                 if (m.type, m.ref) in geo]
        if not boxes:
            continue
        bb = (min(b[0] for b in boxes), min(b[1] for b in boxes),
              max(b[2] for b in boxes), max(b[3] for b in boxes))
        p.handle('relation', r.id, r.tags,
                 (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2, bb)
    del geo, need_way, need_node
    p.flush(force=True)
    p.db.commit()
    print(f'2. menet: {time.time()-t0:.0f}s, {dict(p.stats)}', flush=True)

    # ------------------------------------------------------------ kiírás
    db = p.db
    db.execute('CREATE INDEX rec_tile ON rec(i,j)')
    db.execute('CREATE INDEX sets_tile ON sets(i,j)')
    db.execute('CREATE INDEX hist_tile ON hist(i,j)')

    blobs = []
    offset = 0
    header = {
        'format': 1,
        'id': region_id,
        'name': {'en': name_en},
        'built': time.strftime('%Y-%m-%d'),
        'extract': extract_date,
        'source': f'© OpenStreetMap contributors — Geofabrik {extract_date}',
        'licence': 'ODbL-1.0',
        'licenceUrl': 'https://opendatacommons.org/licenses/odbl/1-0/',
        'copyrightUrl': 'https://www.openstreetmap.org/copyright',
        'recStep': REC,
        'tagStep': TAG,
        'bbox': [round(x, 4) for x in p.bbox],
        'rec': {},
        'tag': {},
    }

    def add(data):
        nonlocal offset
        z = gzip.compress(data, 6, mtime=0)
        blobs.append(z)
        here = offset
        offset += len(z)
        return [here, len(z)]

    # rec: csempénként egy tömb JSON-sor.
    cur = db.execute('SELECT i, j, line FROM rec ORDER BY i, j')
    key = None
    lines = []
    for i, j, line in cur:
        if (i, j) != key:
            if key is not None:
                header['rec'][f'{key[0]},{key[1]}'] = add(
                    '\n'.join(lines).encode())
            key = (i, j)
            lines = []
        lines.append(line)
    if key is not None:
        header['rec'][f'{key[0]},{key[1]}'] = add('\n'.join(lines).encode())
    lines = None

    # tag: a 0,01°-os cellák 0,05°-os tömbökbe fogva. Külön-külön tömörítve
    # a sok apró darab másfélszer ennyi helyet foglalna.
    buckets = collections.defaultdict(dict)
    for i, j, tags, n in db.execute(
            'SELECT i, j, tags, COUNT(*) FROM sets GROUP BY i, j, tags'):
        cell = buckets[(fl((i + 0.5) * TAG, REC), fl((j + 0.5) * TAG, REC))]
        entry = cell.setdefault(f'{i},{j}', {'s': [], 'h': {}})
        entry['s'].append([json.loads(tags), n])
    for i, j, kv, n in db.execute(
            'SELECT i, j, kv, COUNT(*) FROM hist GROUP BY i, j, kv'):
        cell = buckets[(fl((i + 0.5) * TAG, REC), fl((j + 0.5) * TAG, REC))]
        entry = cell.setdefault(f'{i},{j}', {'s': [], 'h': {}})
        entry['h'][kv] = n
    for (i, j), group in sorted(buckets.items()):
        header['tag'][f'{i},{j}'] = add(
            json.dumps(group, ensure_ascii=False,
                       separators=(',', ':')).encode())
    buckets = None

    large = [row[0] for row in db.execute('SELECT line FROM large')]
    header['large'] = add('\n'.join(large).encode())
    large = None
    db.close()
    os.remove(db_path)

    hz = gzip.compress(json.dumps(header, ensure_ascii=False,
                                  separators=(',', ':')).encode(), 9, mtime=0)
    path = os.path.join(out_dir, f'{region_id}.twpack')
    sha = hashlib.sha256()
    with open(path, 'wb') as f:
        for part in (b'TWPK', struct.pack('>BI', 1, len(hz)), hz):
            f.write(part)
            sha.update(part)
        for z in blobs:
            f.write(z)
            sha.update(z)

    entry = {
        'id': region_id,
        'name': {'en': name_en},
        'version': extract_date.replace('-', ''),
        'file': f'{region_id}.twpack',
        'bytes': os.path.getsize(path),
        'sha256': sha.hexdigest(),
        'bbox': header['bbox'],
        'licence': 'ODbL-1.0',
    }
    with open(os.path.join(out_dir, f'{region_id}.json'), 'w',
              encoding='utf-8') as f:
        json.dump(entry, f, ensure_ascii=False)
    print(json.dumps({**entry, 'recTiles': len(header['rec']),
                      'tagTiles': len(header['tag']),
                      'seconds': round(time.time() - t0)}, ensure_ascii=False))


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
          os.environ.get('TWPACK_DB', 'build.sqlite'))
