'use client';

import { useEffect, useRef } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { ClimateCell, fetchRegionBoundaries } from '@/lib/api';

maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');

export function ClimateMap({ cells }: { cells: ClimateCell[] }) {
	const mapContainer = useRef<HTMLDivElement>(null);
	const map = useRef<maplibregl.Map | null>(null);

	// 1. Инициализация карты
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

	// 2. Отрисовка
	useEffect(() => {
		const mapCur = map.current;
		if (!mapCur || !cells.length) return;

		const updateMap = async () => {
			if (!mapCur.isStyleLoaded()) return;

			try {
				// Получаем границы субъектов СКФО
				const boundaries = await fetchRegionBoundaries();

				// Считаем цвет для каждого региона
				const regionColors: Record<string, string> = {};
				
				boundaries.features.forEach((feature: any) => {
					let centerLon = 0, centerLat = 0;

					// Если бэкенд прислал centroid, используем его
					if (feature.properties.centroid && feature.properties.centroid.lon) {
						centerLon = feature.properties.centroid.lon;
						centerLat = feature.properties.centroid.lat;
					} else if (feature.geometry && feature.geometry.coordinates) {
						// Иначе вычисляем центр по первой точке полигона
						// (для простоты берем первую точку первого кольца)
						const coords = feature.geometry.type === 'Polygon' 
							? feature.geometry.coordinates[0] 
							: feature.geometry.coordinates[0][0];
						if (coords && coords.length > 0) {
							centerLon = coords[0][0];
							centerLat = coords[0][1];
						}
					}

					if (centerLon && centerLat) {
						// Находим ближайшую климатическую ячейку к центру региона
						let minDist = Infinity;
						let closestColor = '#cccccc';
						
						cells.forEach(c => {
							const d = Math.sqrt(Math.pow(c.grid_lon - centerLon, 2) + Math.pow(c.grid_lat - centerLat, 2));
							if (d < minDist) {
								minDist = d;
								closestColor = c.color === 'green' ? '#22c55e' : c.color === 'yellow' ? '#eab308' : '#ef4444';
							}
						});
						regionColors[feature.properties.region_code || feature.properties.name] = closestColor;
					}
				});

				// Добавляем источник, если его нет
				if (!mapCur.getSource('skfo-regions')) {
					mapCur.addSource('skfo-regions', { type: 'geojson', data: boundaries });
					
					const matchExpr = [
						'match',
						['get', 'region_code'], // Или 'name', если region_code нет
						...Object.entries(regionColors).flatMap(([code, color]) => [code, color]),
						'#e5e7eb',
					] as any;

					mapCur.addLayer({
						id: 'skfo-regions-fill',
						type: 'fill',
						source: 'skfo-regions',
						paint: {
							'fill-color': matchExpr,
							'fill-opacity': 0.6,
						},
					});

					// Слой обводки (границы регионов)
					mapCur.addLayer({
						id: 'skfo-regions-border',
						type: 'line',
						source: 'skfo-regions',
						paint: {
							'line-color': '#ffffff',
							'line-width': 2,
							'line-opacity': 0.8,
						},
					});
				} else {
					const matchExpr = [
						'match',
						['get', 'region_code'],
						...Object.entries(regionColors).flatMap(([code, color]) => [code, color]),
						'#e5e7eb',
					] as any;

					mapCur.setPaintProperty('skfo-regions-fill', 'fill-color', matchExpr);
				}
			} catch (e) {
				console.error('Ошибка при загрузке границ:', e);
			}
		};

		updateMap();
	}, [cells]);

	return <div ref={mapContainer} className="absolute inset-0 w-full h-full" />;
}