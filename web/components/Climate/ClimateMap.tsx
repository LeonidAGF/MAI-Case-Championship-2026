'use client';

import { useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { ClimateCell, ClimateTarget, RegionCollection, RegionFeature } from '@/lib/api';
import { buildClimateSurface, ClimateMode, ClimateSurface } from '@/lib/climate';
import { colorAt, TEMPERATURE_STOPS } from '@/lib/palette';
import { Icon, IconName } from '@/components/UI/Icon';
import { Spinner } from '@/components/UI/Spinner';
import { formatTemperature, StatusPill } from '@/components/Climate/ClimateStatus';

maplibregl.setWorkerUrl('/maplibre/maplibre-gl-worker.mjs');

const STYLE_URL = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json';
const SURFACE_OPACITY = 0.86;
const EMPTY = { type: 'FeatureCollection' as const, features: [] };

type Slot = 'a' | 'b';
type Bounds = [[number, number], [number, number]];

/** Fly to a region; `nonce` lets the same region be focused again. */
export interface RegionFocus {
	code: string;
	nonce: number;
}

interface Tooltip {
	x: number;
	y: number;
	flip: boolean;
	cell: ClimateCell;
}

function boundsOf(features: RegionFeature[]): Bounds | null {
	let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
	for (const f of features) {
		const g = f.geometry;
		if (!g) continue;
		const polygons = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
		for (const polygon of polygons) {
			for (const [lon, lat] of polygon[0]) {
				w = Math.min(w, lon);
				e = Math.max(e, lon);
				s = Math.min(s, lat);
				n = Math.max(n, lat);
			}
		}
	}
	return Number.isFinite(w) ? [[w, s], [e, n]] : null;
}

/** Room for the floating panels: the top bar, the legend and, on wide screens, the regions list. */
function framePadding(m: maplibregl.Map): maplibregl.PaddingOptions {
	return m.getContainer().clientWidth >= 768
		? { top: 110, bottom: 90, left: 60, right: 380 }
		: { top: 170, bottom: 230, left: 24, right: 24 };
}

/** Resolves once an image source has its new picture (or after a short timeout). */
function whenSourceLoaded(m: maplibregl.Map, id: string): Promise<void> {
	return new Promise((resolve) => {
		const onData = (e: maplibregl.MapSourceDataEvent) => {
			if (e.sourceId === id && e.isSourceLoaded) finish();
		};
		const timer = window.setTimeout(() => finish(), 900);
		function finish() {
			window.clearTimeout(timer);
			m.off('sourcedata', onData);
			resolve();
		}
		m.on('sourcedata', onData);
	});
}

const revoke = (url: string | undefined) => {
	if (url?.startsWith('blob:')) URL.revokeObjectURL(url);
};

export function ClimateMap({
	cells,
	target,
	regions,
	mode,
	highlight,
	focus,
}: {
	cells: ClimateCell[];
	target: ClimateTarget;
	regions: RegionCollection;
	mode: ClimateMode;
	highlight: string | null;
	focus: RegionFocus | null;
}) {
	const container = useRef<HTMLDivElement>(null);
	const map = useRef<maplibregl.Map | null>(null);
	const surface = useRef<ClimateSurface | null>(null);
	const front = useRef<Slot>('a');
	const urls = useRef<Record<Slot, string | undefined>>({ a: undefined, b: undefined });
	const [ready, setReady] = useState(false);
	const [built, setBuilt] = useState<{ cells: ClimateCell[]; mode: ClimateMode } | null>(null);
	const [tooltip, setTooltip] = useState<Tooltip | null>(null);

	// 1. The map and the pointer readout.
	useEffect(() => {
		if (map.current || !container.current) return;
		const m = new maplibregl.Map({
			container: container.current,
			style: STYLE_URL,
			center: [44.4, 43.7],
			zoom: 6.3,
			minZoom: 4.5,
			maxZoom: 11,
			attributionControl: false,
		});
		m.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-left');
		map.current = m;
		m.on('load', () => setReady(true));

		let frame = 0;
		let last: maplibregl.MapMouseEvent | null = null;
		const clear = () => {
			setTooltip(null);
			(m.getSource('hover-node') as maplibregl.GeoJSONSource | undefined)?.setData(EMPTY);
			m.getCanvas().style.cursor = '';
		};
		m.on('mousemove', (e) => {
			last = e;
			if (frame) return;
			frame = requestAnimationFrame(() => {
				frame = 0;
				const s = surface.current;
				if (!s || !last) return;
				const { lng, lat } = last.lngLat;
				const cell = s.inside(lng, lat) ? s.nearest(lng, lat) : null;
				if (!cell) return clear();
				setTooltip({ x: last.point.x, y: last.point.y, flip: last.point.x > m.getContainer().clientWidth - 290, cell });
				(m.getSource('hover-node') as maplibregl.GeoJSONSource | undefined)?.setData({
					type: 'FeatureCollection',
					features: [{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [cell.grid_lon, cell.grid_lat] } }],
				});
				m.getCanvas().style.cursor = 'crosshair';
			});
		});
		m.on('mouseout', () => {
			cancelAnimationFrame(frame);
			frame = 0;
			clear();
		});

		const slots = urls.current;
		return () => {
			cancelAnimationFrame(frame);
			m.remove();
			map.current = null;
			revoke(slots.a);
			if (slots.b !== slots.a) revoke(slots.b);
		};
	}, []);

	// 2. Region borders, isolines and the hovered grid node, under the basemap labels.
	useEffect(() => {
		const m = map.current;
		if (!ready || !m) return;
		const existing = m.getSource('regions') as maplibregl.GeoJSONSource | undefined;
		if (existing) {
			existing.setData(regions);
			return;
		}
		const beforeId = m.getStyle().layers.find((l) => l.type === 'symbol')?.id;
		m.addSource('regions', { type: 'geojson', data: regions });
		m.addSource('isolines', { type: 'geojson', data: EMPTY });
		m.addSource('hover-node', { type: 'geojson', data: EMPTY });
		const major: maplibregl.ExpressionSpecification = ['get', 'major'];
		m.addLayer({
			id: 'isolines',
			type: 'line',
			source: 'isolines',
			layout: { 'line-cap': 'round', 'line-join': 'round' },
			paint: {
				'line-color': '#ffffff',
				'line-opacity': ['case', major, 0.9, 0.5],
				'line-width': ['interpolate', ['linear'], ['zoom'], 5, ['case', major, 1.4, 0.7], 9, ['case', major, 2.6, 1.3]],
			},
		}, beforeId);
		m.addLayer({ id: 'regions-glow', type: 'line', source: 'regions', paint: { 'line-color': '#171b35', 'line-opacity': 0.16, 'line-width': 6, 'line-blur': 5 } }, beforeId);
		m.addLayer({
			id: 'regions-line',
			type: 'line',
			source: 'regions',
			layout: { 'line-join': 'round' },
			paint: { 'line-color': '#ffffff', 'line-opacity': 0.95, 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1.2, 9, 2.6] },
		}, beforeId);
		m.addLayer({
			id: 'regions-highlight',
			type: 'line',
			source: 'regions',
			filter: ['==', ['get', 'region_code'], ''],
			layout: { 'line-join': 'round' },
			paint: { 'line-color': '#775cf8', 'line-width': 3.5, 'line-opacity': 0.95 },
		}, beforeId);
		m.addLayer({
			id: 'hover-node',
			type: 'circle',
			source: 'hover-node',
			paint: { 'circle-radius': 6, 'circle-color': 'rgba(255,255,255,0.3)', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2.5 },
		});
		const b = boundsOf(regions.features);
		if (b) m.fitBounds(b, { padding: framePadding(m), duration: 0 });
	}, [ready, regions]);

	// 3. The surface: rebuilt on a new season or mode, cross-faded over the previous one.
	useEffect(() => {
		const m = map.current;
		if (!ready || !m || !cells.length) return;
		let cancelled = false;

		buildClimateSurface(cells, regions, mode, target).then((s) => {
			if (cancelled || map.current !== m) {
				revoke(s.url);
				return;
			}
			surface.current = s;
			(m.getSource('isolines') as maplibregl.GeoJSONSource | undefined)?.setData(s.isolines);

			if (!m.getSource('climate-a')) {
				for (const slot of ['a', 'b'] as const) {
					m.addSource(`climate-${slot}`, { type: 'image', url: s.url, coordinates: s.coordinates });
					m.addLayer({
						id: `climate-${slot}`,
						type: 'raster',
						source: `climate-${slot}`,
						paint: {
							'raster-opacity': slot === 'a' ? SURFACE_OPACITY : 0,
							'raster-opacity-transition': { duration: 700, delay: 0 },
							'raster-resampling': 'linear',
							'raster-fade-duration': 0,
						},
					}, 'isolines');
				}
				urls.current = { a: s.url, b: s.url };
				front.current = 'a';
			} else {
				const back: Slot = front.current === 'a' ? 'b' : 'a';
				const replaced = urls.current[back];
				(m.getSource(`climate-${back}`) as maplibregl.ImageSource).updateImage({ url: s.url, coordinates: s.coordinates });
				urls.current[back] = s.url;
				whenSourceLoaded(m, `climate-${back}`).then(() => {
					if (map.current !== m) return;
					const other: Slot = back === 'a' ? 'b' : 'a';
					m.setPaintProperty(`climate-${back}`, 'raster-opacity', SURFACE_OPACITY);
					m.setPaintProperty(`climate-${other}`, 'raster-opacity', 0);
					front.current = back;
					if (replaced !== urls.current.a && replaced !== urls.current.b) revoke(replaced);
				});
			}
			setBuilt({ cells, mode });
		}).catch((e) => console.error('Ошибка построения климатической поверхности:', e));

		return () => {
			cancelled = true;
		};
	}, [ready, cells, regions, mode, target]);

	useEffect(() => {
		const m = map.current;
		if (!ready || !m?.getLayer('regions-highlight')) return;
		m.setFilter('regions-highlight', ['==', ['get', 'region_code'], highlight ?? '']);
	}, [ready, highlight]);

	useEffect(() => {
		const m = map.current;
		if (!ready || !m || !focus) return;
		const b = boundsOf(regions.features.filter((f) => f.properties.region_code === focus.code));
		if (b) m.fitBounds(b, { padding: framePadding(m), duration: 900, maxZoom: 8.5 });
	}, [ready, focus, regions]);

	const building = !built || built.cells !== cells || built.mode !== mode;

	return (
		<div className="absolute inset-0">
			<div ref={container} className="h-full w-full" />
			<div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_160px_rgb(23_27_53/0.10)]" />

			{building && (
				<div className="glass pointer-events-none absolute left-1/2 top-1/2 z-10 flex -translate-x-1/2 -translate-y-1/2 items-center gap-2.5 rounded-full px-4 py-2.5 text-sm font-semibold text-ink-700 animate-fade-in">
					<Spinner className="h-4 w-4 text-brand-500" />
					Строим климатическую поверхность…
				</div>
			)}

			{tooltip && <NodeTooltip tooltip={tooltip} />}
		</div>
	);
}

function NodeTooltip({ tooltip }: { tooltip: Tooltip }) {
	const { cell } = tooltip;
	return (
		<div
			className="glass-strong pointer-events-none absolute z-20 w-64 rounded-2xl p-3.5"
			style={{
				left: tooltip.x,
				top: tooltip.y,
				transform: tooltip.flip ? 'translate(calc(-100% - 18px), -50%)' : 'translate(18px, -50%)',
			}}
		>
			<div className="flex items-baseline justify-between gap-2">
				<span className="text-[11px] font-bold uppercase tracking-[0.12em] text-ink-400">Узел сетки</span>
				<span className="text-[11px] font-semibold tabular-nums text-ink-400">
					{cell.grid_lat.toFixed(2)}° с. ш. · {cell.grid_lon.toFixed(2)}° в. д.
				</span>
			</div>
			<div className="mt-2.5 flex items-center gap-3">
				<span className="h-10 w-10 shrink-0 rounded-xl shadow-soft ring-2 ring-white" style={{ background: colorAt(TEMPERATURE_STOPS, cell.temperature_c) }} />
				<div>
					<div className="font-display text-2xl font-bold leading-none tracking-tight tabular-nums text-ink-900">{formatTemperature(cell.temperature_c)}</div>
					<div className="mt-1 text-[11px] font-semibold text-ink-500">средняя температура сезона</div>
				</div>
			</div>
			<div className="mt-3 grid grid-cols-2 gap-2">
				<Metric icon="droplet" label="Влажность" value={`${cell.humidity_pct.toFixed(0)}%`} />
				<Metric icon="wind" label="Ветер" value={`${cell.wind_m_s.toFixed(1)} м/с`} />
			</div>
			<div className="mt-3 flex items-center justify-between gap-2">
				<span className="text-[11px] font-semibold text-ink-400">К цели сезона</span>
				<StatusPill color={cell.color} />
			</div>
		</div>
	);
}

function Metric({ icon, label, value }: { icon: IconName; label: string; value: string }) {
	return (
		<div className="rounded-xl bg-ink-50 px-2.5 py-2 ring-1 ring-ink-100">
			<div className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.1em] text-ink-400">
				<Icon name={icon} className="h-3 w-3" />
				{label}
			</div>
			<div className="mt-0.5 text-sm font-bold tabular-nums text-ink-800">{value}</div>
		</div>
	);
}
