from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
INPUT = ROOT / 'data' / 'processed' / 'roads'
OUTPUT = ROOT / 'graphs' / 'base' / 'v1'
REGIONS = ('north-caucasus-fed-district',)
BATCH = 20000

DEFAULT_SPEED = {
    'motorway': 90, 'motorway_link': 50, 'trunk': 80, 'trunk_link': 45,
    'primary': 70, 'primary_link': 40, 'secondary': 60, 'secondary_link': 35,
    'tertiary': 50, 'tertiary_link': 30, 'unclassified': 40,
    'residential': 30, 'living_street': 15, 'service': 20,
    'road': 30, 'track': 15,
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def scalar(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else None
    return str(v)


def parse_oneway(v, junction):
    x = (scalar(v) or '').strip().lower()
    if x in ('yes', 'true', '1'):
        return 1
    if x in ('-1', 'reverse'):
        return -1
    if x in ('no', 'false', '0'):
        return 0
    if (scalar(junction) or '').strip().lower() == 'roundabout':
        return 1
    return 0


def speed_kmh(maxspeed, highway):
    import re
    x = (scalar(maxspeed) or '').lower().strip()
    match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(km/h|kmh|kph|mph)?', x)
    if match:
        value = float(match.group(1).replace(',', '.'))
        if match.group(2) == 'mph':
            value *= 1.609344
        if 1 <= value <= 160:
            return value, 'osm_maxspeed'
    kind = (scalar(highway) or '').split(';')[0].strip()
    return float(DEFAULT_SPEED.get(kind, 30)), 'assumed_by_highway'


def haversine_m(lon1, lat1, lon2, lat2):
    r = 6371008.8
    a1, a2 = math.radians(lat1), math.radians(lat2)
    da = a2 - a1
    db = math.radians(lon2 - lon1)
    q = math.sin(da / 2) ** 2 + math.cos(a1) * math.cos(a2) * math.sin(db / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(q)))


def atomic_json(path, obj):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def check_inputs():
    for region in REGIONS:
        for filename in ('ways.parquet', 'nodes.parquet'):
            p = INPUT / region / filename
            if not p.is_file():
                raise FileNotFoundError(f'Отсутствует {p}')
    node_cols = {'node_id', 'lon', 'lat'}
    way_cols = {'osm_way_id', 'node_ids', 'highway'}
    for region in REGIONS:
        ns = set(pq.ParquetFile(INPUT / region / 'nodes.parquet').schema_arrow.names)
        ws = set(pq.ParquetFile(INPUT / region / 'ways.parquet').schema_arrow.names)
        if not node_cols <= ns or not way_cols <= ws:
            raise ValueError(f'Неожиданная схема Parquet для {region}: nodes={ns}, ways={ws}')


def create_db(db_path):
    db = sqlite3.connect(db_path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=NORMAL')
    db.execute('PRAGMA cache_size=-64000')
    db.execute('CREATE TABLE nodes (id INTEGER PRIMARY KEY, lon REAL NOT NULL, lat REAL NOT NULL)')
    db.execute('CREATE TABLE ways (id INTEGER PRIMARY KEY, region TEXT NOT NULL)')
    return db


def load_nodes(db, stats):
    print('1/3 Загружаем координаты узлов в SQLite...', flush=True)
    for region in REGIONS:
        path = INPUT / region / 'nodes.parquet'
        inserted = 0
        duplicates = 0
        missing = 0
        with db:
            for batch in pq.ParquetFile(path).iter_batches(batch_size=BATCH, columns=['node_id', 'lon', 'lat']):
                ids, lons, lats = [col.to_pylist() for col in batch.columns]
                rows = []
                for nid, lon, lat in zip(ids, lons, lats):
                    if nid is None or lon is None or lat is None:
                        missing += 1
                        continue
                    if not (-180 <= lon <= 180 and -90 <= lat <= 90 and math.isfinite(lon) and math.isfinite(lat)):
                        missing += 1
                        continue
                    rows.append((int(nid), float(lon), float(lat)))
                before = db.total_changes
                db.executemany('INSERT OR IGNORE INTO nodes VALUES (?, ?, ?)', rows)
                new = db.total_changes - before
                inserted += new
                duplicates += len(rows) - new
        stats['nodes_by_region'][region] = {'inserted': inserted, 'duplicate_osm_ids': duplicates, 'invalid_coordinates': missing}
        print(f'  {region}: новых {inserted:,}, дублей {duplicates:,}, некорректных {missing:,}', flush=True)
    stats['unique_nodes'] = db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]


def build_edges(db, stats, temp_path):
    schema = pa.schema([
        ('u', pa.int64()), ('v', pa.int64()), ('osm_way_id', pa.int64()),
        ('length_m', pa.float64()), ('travel_time_s', pa.float64()),
        ('speed_kmh', pa.float64()), ('speed_source', pa.string()),
        ('highway', pa.string()), ('name', pa.string()), ('ref', pa.string()),
        ('oneway', pa.string()), ('maxspeed', pa.string()), ('surface', pa.string()),
        ('access', pa.string()), ('motor_vehicle', pa.string()),
        ('bridge', pa.string()), ('tunnel', pa.string()), ('junction', pa.string()),
        ('region', pa.string()),
    ])
    writer = pq.ParquetWriter(temp_path, schema, compression='zstd')
    pending = []
    cache = {}
    cache_limit = 100000

    def flush():
        if pending:
            writer.write_table(pa.Table.from_pylist(pending, schema=schema))
            pending.clear()

    def coord(nid):
        if nid in cache:
            return cache[nid]
        item = db.execute('SELECT lon, lat FROM nodes WHERE id=?', (nid,)).fetchone()
        if len(cache) >= cache_limit:
            cache.clear()
        cache[nid] = item
        return item

    print('2/3 Строим направленные дорожные рёбра...', flush=True)
    try:
        for region in REGIONS:
            region_stats = {'ways_seen': 0, 'unique_ways': 0, 'duplicate_ways': 0,
                            'invalid_ways': 0, 'missing_node_pairs': 0,
                            'zero_length_pairs': 0, 'directed_edges': 0}
            path = INPUT / region / 'ways.parquet'
            for batch in pq.ParquetFile(path).iter_batches(batch_size=5000):
                for way in batch.to_pylist():
                    region_stats['ways_seen'] += 1
                    wid = way.get('osm_way_id')
                    ids = way.get('node_ids')
                    if wid is None or not ids or len(ids) < 2:
                        region_stats['invalid_ways'] += 1
                        continue
                    wid = int(wid)
                    cursor = db.execute('INSERT OR IGNORE INTO ways VALUES (?, ?)', (wid, region))
                    if cursor.rowcount == 0:
                        region_stats['duplicate_ways'] += 1
                        continue
                    region_stats['unique_ways'] += 1
                    direction = parse_oneway(way.get('oneway'), way.get('junction'))
                    speed, speed_source = speed_kmh(way.get('maxspeed'), way.get('highway'))
                    common = {k: scalar(way.get(k)) for k in (
                        'highway', 'name', 'ref', 'oneway', 'maxspeed', 'surface',
                        'access', 'motor_vehicle', 'bridge', 'tunnel', 'junction')}
                    common.update(osm_way_id=wid, speed_kmh=speed, speed_source=speed_source, region=region)
                    for a, b in zip(ids, ids[1:]):
                        if a is None or b is None:
                            region_stats['missing_node_pairs'] += 1
                            continue
                        a, b = int(a), int(b)
                        if a == b:
                            region_stats['zero_length_pairs'] += 1
                            continue
                        ca, cb = coord(a), coord(b)
                        if ca is None or cb is None:
                            region_stats['missing_node_pairs'] += 1
                            continue
                        length = haversine_m(ca[0], ca[1], cb[0], cb[1])
                        if not math.isfinite(length) or length < 0.01:
                            region_stats['zero_length_pairs'] += 1
                            continue
                        values = dict(common, length_m=length, travel_time_s=length / (speed / 3.6))
                        if direction >= 0:
                            pending.append(dict(values, u=a, v=b))
                            region_stats['directed_edges'] += 1
                        if direction <= 0:
                            pending.append(dict(values, u=b, v=a))
                            region_stats['directed_edges'] += 1
                        if len(pending) >= BATCH:
                            flush()
                if region_stats['ways_seen'] % 50000 < 5000:
                    print(f"  {region}: обработано ways {region_stats['ways_seen']:,}", flush=True)
            flush()
            stats['edges_by_region'][region] = region_stats
            print(f"  {region}: {region_stats['directed_edges']:,} направленных рёбер", flush=True)
    finally:
        writer.close()


def export_nodes(db, output, stats):
    print('3/3 Сохраняем уникальные узлы...', flush=True)
    schema = pa.schema([('node_id', pa.int64()), ('lon', pa.float64()), ('lat', pa.float64())])
    writer = pq.ParquetWriter(output, schema, compression='zstd')
    try:
        cursor = db.execute('SELECT id, lon, lat FROM nodes ORDER BY id')
        while True:
            rows = cursor.fetchmany(BATCH)
            if not rows:
                break
            writer.write_table(pa.Table.from_arrays([
                pa.array([r[0] for r in rows], type=pa.int64()),
                pa.array([r[1] for r in rows], type=pa.float64()),
                pa.array([r[2] for r in rows], type=pa.float64()),
            ], schema=schema))
    finally:
        writer.close()
    stats['unique_ways'] = db.execute('SELECT COUNT(*) FROM ways').fetchone()[0]
    stats['directed_edges'] = sum(v['directed_edges'] for v in stats['edges_by_region'].values())


def main():
    print('Этап 3: построение базового транспортного графа', flush=True)
    check_inputs()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    db_path = OUTPUT / 'build_work.sqlite'
    for name in ('build_work.sqlite', 'build_work.sqlite-wal', 'build_work.sqlite-shm'):
        (OUTPUT / name).unlink(missing_ok=True)
    temp_nodes = OUTPUT / 'nodes.parquet.part'
    temp_edges = OUTPUT / 'edges.parquet.part'
    temp_nodes.unlink(missing_ok=True)
    temp_edges.unlink(missing_ok=True)
    stats = {'created_utc': utc_now(), 'regions': list(REGIONS), 'nodes_by_region': {},
             'edges_by_region': {}, 'length_method': 'haversine_between_consecutive_osm_nodes',
             'speed_model': 'OSM numeric maxspeed if available; otherwise assumed speed by highway class',
             'warnings': [
                 'Graph is directed and retains all OSM polyline vertices, not only intersections.',
                 'Access tags are preserved but not yet interpreted for routing; non-car-access ways may remain.',
                 'Turn restrictions and conditional oneway restrictions are not modeled.',
                 'Cross-region duplicate ways are deduplicated by OSM way ID; conflicting copies are not reconciled.',
                 'No inter-region edges are invented; border connectivity must be validated.',
                 'Travel times are estimates, not measured traffic conditions.',
             ]}
    db = create_db(db_path)
    start = time.monotonic()
    try:
        load_nodes(db, stats)
        build_edges(db, stats, temp_edges)
        export_nodes(db, temp_nodes, stats)
        db.commit()
    finally:
        db.close()
    os.replace(temp_nodes, OUTPUT / 'nodes.parquet')
    os.replace(temp_edges, OUTPUT / 'edges.parquet')
    stats['elapsed_seconds'] = round(time.monotonic() - start, 1)
    stats['finished_utc'] = utc_now()
    atomic_json(OUTPUT / 'build_manifest.json', stats)
    print(f"Готово! Узлов: {stats['unique_nodes']:,}; направленных рёбер: {stats['directed_edges']:,}", flush=True)
    print(f'Результаты: {OUTPUT}', flush=True)
    print('Примечание: build_work.sqlite оставлен для возможной диагностики; его можно удалить после проверки.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ОШИБКА: {exc}', file=sys.stderr, flush=True)
        raise