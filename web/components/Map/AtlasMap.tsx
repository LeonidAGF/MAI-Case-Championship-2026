'use client';

import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { useQuery } from '@tanstack/react-query';
import { useAtlas } from '@/lib/store';
import { API } from '@/lib/api';
import { encodeProfile } from '@/lib/profile';

maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');

const COLOR_STOPS: [number, string][] = [
  [0, '#f7fbff'], [30, '#c6dbef'], [55, '#6baed6'], [75, '#2171b5'], [100, '#08306b'],
];

export function AtlasMap() {
	const mapContainer = useRef<HTMLDivElement>(null);
	const map = useRef<maplibregl.Map | null>(null);
	
	const selectTerritory = useAtlas((s) => s.selectTerritory);
	const profile = useAtlas((s) => s.profile);

	const { data: geojson, isLoading } = useQuery({
		queryKey: ['geojson', encodeProfile(profile)],
		queryFn: async () => {
			const q = new URLSearchParams({ level: 'settlement', with_index: 'true', p: encodeProfile(profile) });
			const res = await fetch(`${API}/territories/geojson?${q}`);
			if (!res.ok) throw new Error('Failed to load GeoJSON');
			return res.json();
		},
	});

	useEffect(() => {
		if (map.current || !mapContainer.current) return;

		map.current = new maplibregl.Map({
			container: mapContainer.current,
			style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
			center: [44.0, 43.5],
			zoom: 6.5,
		});

		map.current.on('load', () => {
			if (!map.current) return;
			
			map.current.addSource('settlements', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
			
			map.current.addLayer({
				id: 'settlements-circle',
				type: 'circle',
				source: 'settlements',
				paint: {
					'circle-radius': 5,
					'circle-color': '#eeeeee',
					'circle-stroke-width': 1,
					'circle-stroke-color': '#ffffff'
				}
			});
		});

		map.current.on('click', 'settlements-circle', (e) => {
			const id = e.features?.[0]?.properties?.id;
			if (id != null) selectTerritory(Number(id));
		});

		map.current.on('mouseenter', 'settlements-circle', () => { if (map.current) map.current.getCanvas().style.cursor = 'pointer'; });
		map.current.on('mouseleave', 'settlements-circle', () => { if (map.current) map.current.getCanvas().style.cursor = ''; });

		return () => { 
			map.current?.remove(); 
			map.current = null; 
		};
	}, [selectTerritory]);

	useEffect(() => {
		const mapCur = map.current;
		if (!mapCur || !geojson) return;

		const updateMap = () => {
			if (!mapCur.getSource('settlements')) return;

			(mapCur.getSource('settlements') as maplibregl.GeoJSONSource).setData(geojson);

			const matchExpr: any = ['match', ['get', 'id']];
			geojson.features.forEach((f: any) => {
				const total = f.properties.total;
				if (total != null) {
					matchExpr.push(f.properties.id, colorFor(total));
				}
			});
			matchExpr.push('#eeeeee');

			if (mapCur.getLayer('settlements-circle')) {
				mapCur.setPaintProperty('settlements-circle', 'circle-color', matchExpr);
			}
		};

		if (mapCur.loaded()) {
			updateMap();
		} else {
			mapCur.once('load', updateMap);
		}
	}, [geojson]);

	return (
		<div className="absolute inset-0 w-full h-full">
			<div ref={mapContainer} className="w-full h-full" />
			{isLoading && (
				<div className="absolute inset-0 flex items-center justify-center bg-white/50 backdrop-blur-sm z-10">
					<div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
				</div>
			)}
		</div>
	);
}

function colorFor(score: number): string {
	for (let i = COLOR_STOPS.length - 1; i >= 0; i--) {
		if (score >= COLOR_STOPS[i][0]) return COLOR_STOPS[i][1];
	}
	return COLOR_STOPS[0][1];
}