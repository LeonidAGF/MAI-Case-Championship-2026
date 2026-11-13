'use client';

import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useAtlas } from '@/lib/store';
import { fetchSettlementsGeoJSON, SettlementCollection } from '@/lib/api';
import { encodeProfile } from '@/lib/profile';
import { colorAt, cssGradient, INDEX_DOMAIN, INDEX_STOPS, NO_DATA_COLOR, stopsForMap } from '@/lib/palette';
import { Spinner } from '@/components/UI/Spinner';

maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');

const STYLE_URL = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';
const EMPTY: SettlementCollection = { type: 'FeatureCollection', features: [] };

// Colour by the index, blended in OKLab like the legend; settlements without a score are gray.
const SCORE_COLOR = [
	'case',
	['==', ['typeof', ['get', 'total']], 'number'],
	['interpolate-lab', ['linear'], ['get', 'total'], ...stopsForMap(INDEX_STOPS)],
	NO_DATA_COLOR,
] as unknown as maplibregl.ExpressionSpecification;
const HOVER = ['boolean', ['feature-state', 'hover'], false];
const RADIUS = [
	'interpolate', ['linear'], ['zoom'],
	5, ['case', HOVER, 4.5, 2.4],
	8, ['case', HOVER, 9, 5.2],
	12, ['case', HOVER, 15, 10],
] as unknown as maplibregl.ExpressionSpecification;

interface Hovered {
	name: string;
	total: number | null;
	x: number;
	y: number;
}

export function AtlasMap() {
	const container = useRef<HTMLDivElement>(null);
	const map = useRef<maplibregl.Map | null>(null);
	const marker = useRef<maplibregl.Marker | null>(null);
	const [ready, setReady] = useState(false);
	const [hovered, setHovered] = useState<Hovered | null>(null);

	const selectTerritory = useAtlas((s) => s.selectTerritory);
	const selectedId = useAtlas((s) => s.selectedTerritoryId);
	const profile = useAtlas((s) => s.profile);

	const { data: geojson, isFetching } = useQuery({
		queryKey: ['geojson', encodeProfile(profile)],
		queryFn: () => fetchSettlementsGeoJSON(profile),
		placeholderData: keepPreviousData,
	});

	useEffect(() => {
		if (map.current || !container.current) return;

		const m = new maplibregl.Map({
			container: container.current,
			style: STYLE_URL,
			center: [44.0, 43.5],
			zoom: 6.5,
			attributionControl: false,
		});
		m.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left');
		map.current = m;

		let hoverId: number | null = null;
		const setHover = (id: number | null) => {
			if (hoverId !== null) m.setFeatureState({ source: 'settlements', id: hoverId }, { hover: false });
			hoverId = id;
			if (id !== null) m.setFeatureState({ source: 'settlements', id }, { hover: true });
		};

		m.on('load', () => {
			m.addSource('settlements', { type: 'geojson', data: EMPTY, promoteId: 'id' });
			// A soft glow under every point: the map reads as a smooth field instead of scattered dots.
			m.addLayer({
				id: 'settlements-halo',
				type: 'circle',
				source: 'settlements',
				paint: {
					'circle-color': SCORE_COLOR,
					'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 7, 8, 15, 12, 28],
					'circle-blur': 1,
					'circle-opacity': 0.2,
				},
			});
			m.addLayer({
				id: 'settlements-circle',
				type: 'circle',
				source: 'settlements',
				paint: {
					'circle-color': SCORE_COLOR,
					'circle-radius': RADIUS,
					'circle-stroke-color': '#ffffff',
					'circle-stroke-width': ['interpolate', ['linear'], ['zoom'], 5, 0.6, 8, 1.2, 12, 2],
					'circle-stroke-opacity': 0.95,
				},
			});
			setReady(true);
		});

		m.on('click', 'settlements-circle', (e) => {
			const id = e.features?.[0]?.properties?.id;
			if (id != null) selectTerritory(Number(id));
		});
		m.on('mousemove', 'settlements-circle', (e) => {
			const f = e.features?.[0];
			if (!f) return;
			m.getCanvas().style.cursor = 'pointer';
			const id = Number(f.properties?.id);
			if (id !== hoverId) setHover(id);
			const total = f.properties?.total;
			setHovered({ name: String(f.properties?.name ?? ''), total: typeof total === 'number' ? total : null, x: e.point.x, y: e.point.y });
		});
		m.on('mouseleave', 'settlements-circle', () => {
			m.getCanvas().style.cursor = '';
			setHover(null);
			setHovered(null);
		});

		return () => {
			marker.current?.remove();
			marker.current = null;
			m.remove();
			map.current = null;
		};
	}, [selectTerritory]);

	useEffect(() => {
		const m = map.current;
		if (!ready || !m || !geojson) return;
		(m.getSource('settlements') as maplibregl.GeoJSONSource | undefined)?.setData(geojson);
	}, [ready, geojson]);

	// A pulsing ring marks the settlement whose card is open.
	useEffect(() => {
		const m = map.current;
		if (!ready || !m) return;
		const feature = selectedId == null ? undefined : geojson?.features.find((f) => f.properties.id === selectedId);
		if (!feature) {
			marker.current?.remove();
			return;
		}
		if (!marker.current) {
			const el = document.createElement('div');
			el.className = 'pointer-events-none relative h-6 w-6';
			el.innerHTML =
				'<span class="absolute inset-0 rounded-full bg-brand-500/50 animate-pulse-ring"></span>' +
				'<span class="absolute inset-[4px] rounded-full border-[3px] border-white bg-brand-600 shadow-glow"></span>';
			marker.current = new maplibregl.Marker({ element: el });
		}
		marker.current.setLngLat(feature.geometry.coordinates).addTo(m);
	}, [ready, selectedId, geojson]);

	return (
		<div className="absolute inset-0">
			<div ref={container} className="h-full w-full" />
			<div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_160px_rgb(23_27_53/0.10)]" />

			{hovered && (
				<div
					className="glass-strong pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-[calc(100%+14px)] rounded-xl px-3 py-2"
					style={{ left: hovered.x, top: hovered.y }}
				>
					<div className="whitespace-nowrap text-[13px] font-bold text-ink-900">{hovered.name}</div>
					<div className="mt-0.5 flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-ink-500">
						<span className="h-2 w-2 rounded-full" style={{ background: hovered.total == null ? NO_DATA_COLOR : colorAt(INDEX_STOPS, hovered.total) }} />
						{hovered.total == null ? 'нет оценки' : <>Индекс <b className="font-bold tabular-nums text-ink-800">{hovered.total.toFixed(1)}</b></>}
					</div>
				</div>
			)}

			{(isFetching || !geojson) && (
				<div className="glass pointer-events-none absolute bottom-6 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2.5 rounded-full px-4 py-2.5 text-sm font-semibold text-ink-700 animate-fade-in">
					<Spinner className="h-4 w-4 text-brand-500" />
					Пересчитываем индекс…
				</div>
			)}

			<IndexLegend shifted={selectedId != null} />
		</div>
	);
}

function IndexLegend({ shifted }: { shifted: boolean }) {
	return (
		<div
			className={`glass pointer-events-none absolute bottom-10 left-3 z-10 w-56 rounded-2xl px-4 py-3 transition-[left] duration-500 ease-spring animate-fade-up [animation-delay:150ms] md:bottom-11 ${
				shifted ? 'md:left-[27.5rem]' : 'md:left-4'
			}`}
		>
			<div className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-500">Индекс доступности</div>
			<div className="mt-2 h-2.5 rounded-full ring-1 ring-white/80" style={{ backgroundImage: cssGradient(INDEX_STOPS, INDEX_DOMAIN) }} />
			<div className="mt-1.5 flex justify-between text-[11px] font-semibold tabular-nums text-ink-500">
				<span>0</span>
				<span>25</span>
				<span>50</span>
				<span>75</span>
				<span>100</span>
			</div>
		</div>
	);
}
