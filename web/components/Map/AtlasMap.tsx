'use client';

import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useAtlas } from '@/lib/store';
import { fetchClusterIndex, fetchTerritoriesGeoJSON } from '@/lib/api';

maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');

const COLOR_STOPS: [number, string][] = [
  	[0, '#f7fbff'], [30, '#c6dbef'], [55, '#6baed6'], [75, '#2171b5'], [100, '#08306b'],
];

export function AtlasMap() {
	const mapContainer = useRef<HTMLDivElement>(null);
	const map = useRef<maplibregl.Map | null>(null);
	
	const selectTerritory = useAtlas((s) => s.selectTerritory);
	const profile = useAtlas((s) => s.profile);

	useEffect(() => {
		if (map.current || !mapContainer.current) return;

		map.current = new maplibregl.Map({
			container: mapContainer.current,
			style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
			center: [44.0, 43.5],
			zoom: 6.5,
		});

		map.current.on('load', async () => {
		if (!map.current) return;
			try {
				const geojson = await fetchTerritoriesGeoJSON();
				
				map.current.addSource('territories', { type: 'geojson', data: geojson });
				
				map.current.addLayer({
					id: 'territories-fill',
					type: 'fill',
					source: 'territories',
					paint: { 'fill-color': '#eeeeee', 'fill-opacity': 0.6, 'fill-outline-color': '#ffffff' }
				});

				map.current.addLayer({
					id: 'territories-label',
					type: 'symbol',
					source: 'territories',
					layout: { 'text-field': ['get', 'name'], 'text-size': 11 },
					paint: { 'text-color': '#333333', 'text-halo-color': '#ffffff', 'text-halo-width': 1 }
				});
			} catch (e) {
				console.error("Ошибка загрузки GeoJSON:", e);
			}
		});

		map.current.on('click', 'territories-fill', (e) => {
			const id = e.features?.[0]?.properties?.id;
			if (id != null) selectTerritory(Number(id));
		});

		map.current.on('mouseenter', 'territories-fill', () => { if (map.current) map.current.getCanvas().style.cursor = 'pointer'; });
		map.current.on('mouseleave', 'territories-fill', () => { if (map.current) map.current.getCanvas().style.cursor = ''; });

		return () => { map.current?.remove(); map.current = null; };
	}, [selectTerritory]);

	useEffect(() => {
		const mapCur = map.current;
		if (!mapCur || !profile || !profile.weights) return;
		
		let cancelled = false;

		(async () => {
			try {
				const [a, b, v] = await Promise.all([
					fetchClusterIndex('A', profile),
					fetchClusterIndex('B', profile),
					fetchClusterIndex('V', profile),
				]);
				if (cancelled) return;

				const scores: Record<number, number> = {};
				[...a.items, ...b.items, ...v.items].forEach((r) => { scores[r.territory_id] = r.total; });

				const apply = () => {
					if (!mapCur.getLayer('territories-fill')) {
						mapCur.once('load', apply);
						return;
					}
					
					const matchExpr: any = ['match', ['get', 'id']];
					Object.entries(scores).forEach(([id, s]) => {
						matchExpr.push(Number(id), colorFor(s));
					});
					matchExpr.push('#eeeeee');

					mapCur.setPaintProperty('territories-fill', 'fill-color', matchExpr);
				};

				apply(); 
			} catch (e) {
				console.error("Ошибка при перекраске:", e);
			}
		})();

		return () => { cancelled = true; };
	}, [profile]);

	return <div ref={mapContainer} className="absolute inset-0 w-full h-full" />;
}

function colorFor(score: number): string {
	for (let i = COLOR_STOPS.length - 1; i >= 0; i--) {
		if (score >= COLOR_STOPS[i][0]) return COLOR_STOPS[i][1];
	}
	return COLOR_STOPS[0][1];
}