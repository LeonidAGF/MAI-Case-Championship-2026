'use client';

import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { ClimateCell } from '@/lib/api';

maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');

export function ClimateMap({ cells }: { cells: ClimateCell[] }) {
	const mapContainer = useRef<HTMLDivElement>(null);
	const map = useRef<maplibregl.Map | null>(null);

	useEffect(() => {
		if (map.current || !mapContainer.current) return;

		map.current = new maplibregl.Map({
			container: mapContainer.current,
			style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
			center: [44.0, 43.5],
			zoom: 6.5,
		});

		return () => { map.current?.remove(); map.current = null; };
	}, []);

	useEffect(() => {
		const mapCur = map.current;
		if (!mapCur || !cells.length) return;

		const updateMap = () => {
			if (!mapCur.getSource('climate-grid')) {
				const geojson: any = {
					type: 'FeatureCollection',
					features: cells.map((c) => ({
						type: 'Feature',
						properties: {
							color: c.color === 'green' ? '#22c55e' : c.color === 'yellow' ? '#eab308' : '#ef4444',
							temp: c.temperature_c,
						},
						geometry: { type: 'Point', coordinates: [c.grid_lon, c.grid_lat] },
					})),
				};

				mapCur.addSource('climate-grid', { type: 'geojson', data: geojson });

				mapCur.addLayer({
					id: 'climate-heat',
					type: 'circle',
					source: 'climate-grid',
					paint: {
						'circle-radius': 40,
						'circle-color': ['get', 'color'],
						'circle-opacity': 0.4,
						'circle-blur': 1.5,
					},
				});
			} else {
				const geojson: any = {
					type: 'FeatureCollection',
					features: cells.map((c) => ({
						type: 'Feature',
						properties: {
							color: c.color === 'green' ? '#22c55e' : c.color === 'yellow' ? '#eab308' : '#ef4444',
							temp: c.temperature_c,
						},
						geometry: { type: 'Point', coordinates: [c.grid_lon, c.grid_lat] },
					})),
				};
				(mapCur.getSource('climate-grid') as maplibregl.GeoJSONSource).setData(geojson);
			}
		};

		if (mapCur.isStyleLoaded()) {
			updateMap();
		} else {
			mapCur.once('load', updateMap);
		}
	}, [cells]);

	return <div ref={mapContainer} className="absolute inset-0 w-full h-full" />;
}