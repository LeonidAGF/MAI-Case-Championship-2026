'use client';

import { useState, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useAtlas } from '@/lib/store';
import { SUBINDEX_LABELS } from '@/lib/dictionary';
import { Profile, Subindex } from '@/types/profile';
import { Icon } from '@/components/UI/Icon';
import { Segmented, SegmentOption } from '@/components/UI/Segmented';

type Filters = Profile['filters'];
type ChoiceKey = Exclude<keyof Filters, 'realtor_mode'>;

const BASE_WEIGHTS: Record<Subindex, number> = {
	S1: 20, S2: 20, S3: 15, S4: 15, S5: 15, S6: 15,
};

const MENU_WIDTH = 368;

// Labels for the profile filters (values are the contract codes of types/profile.ts).
const FIELDS: { key: ChoiceKey; label: string; options: SegmentOption<string | number>[] }[] = [
	{ key: 'mobility', label: 'Передвижение', options: [{ value: 'car', label: 'На машине' }, { value: 'no_car', label: 'Без машины' }] },
	{ key: 'medicine_threshold_min', label: 'Медицина в пределах', options: [{ value: 15, label: '15 мин' }, { value: 30, label: '30 мин' }, { value: 60, label: '60 мин' }] },
	{ key: 'family', label: 'Семья', options: [{ value: 'single', label: 'Один' }, { value: 'couple', label: 'Пара' }, { value: 'children', label: 'С детьми' }] },
	{ key: 'work', label: 'Работа', options: [{ value: 'offline', label: 'На месте' }, { value: 'remote', label: 'Удалённо' }] },
	{ key: 'travel_frequency', label: 'Дальние поездки', options: [{ value: 'often', label: 'Часто' }, { value: 'rare', label: 'Редко' }, { value: 'none', label: 'Не езжу' }] },
	{ key: 'climate_sensitivity', label: 'Чувствительность к климату', options: [{ value: 'high', label: 'Высокая' }, { value: 'mid', label: 'Средняя' }, { value: 'low', label: 'Низкая' }] },
	{ key: 'territory_types', label: 'Какие территории', options: [{ value: 'cities', label: 'Города' }, { value: 'with_towns', label: '+ райцентры' }, { value: 'all', label: 'Все' }] },
	{ key: 'pace', label: 'Темп жизни', options: [{ value: 'active', label: 'Активный' }, { value: 'calm', label: 'Спокойный' }] },
];

// One colour per subindex for the weights bar.
const WEIGHT_COLORS: Record<Subindex, string> = {
	S1: '#ef6f8c',
	S2: '#8b7bff',
	S3: '#f5b14c',
	S4: '#3fa7e8',
	S5: '#2fbf9b',
	S6: '#a3cf57',
};

export function ProfileBuilder() {
	const { profile, setProfile } = useAtlas();
	const f = profile.filters;

	const [isOpen, setIsOpen] = useState(false);
	const [coords, setCoords] = useState({ top: 0, left: 0 });
	const buttonRef = useRef<HTMLButtonElement>(null);
	const menuRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		if (!isOpen) return;
		const handleClickOutside = (e: MouseEvent) => {
			if (
				buttonRef.current && !buttonRef.current.contains(e.target as Node) &&
				menuRef.current && !menuRef.current.contains(e.target as Node)
			) {
				setIsOpen(false);
			}
		};
		const handleKey = (e: KeyboardEvent) => {
			if (e.key === 'Escape') setIsOpen(false);
		};
		document.addEventListener('mousedown', handleClickOutside);
		document.addEventListener('keydown', handleKey);
		return () => {
			document.removeEventListener('mousedown', handleClickOutside);
			document.removeEventListener('keydown', handleKey);
		};
	}, [isOpen]);

	const toggleMenu = () => {
		if (!isOpen && buttonRef.current) {
			const rect = buttonRef.current.getBoundingClientRect();
			const padding = 16;

			let leftPos = rect.left + window.scrollX;

			const spaceRight = window.innerWidth - rect.left;

			if (spaceRight < MENU_WIDTH + padding) {
				leftPos = rect.right + window.scrollX - MENU_WIDTH;
			}

			if (leftPos < padding) {
				leftPos = padding;
			}

			if (leftPos + MENU_WIDTH > window.innerWidth - padding) {
				leftPos = window.innerWidth - MENU_WIDTH - padding;
			}

			setCoords({
				top: rect.bottom + window.scrollY + 10,
				left: Math.max(8, leftPos),
			});
		}
		setIsOpen(!isOpen);
	};

	const recalculateWeights = (newFilters: Filters) => {
		const w = newFilters.realtor_mode
			? { S1: 15, S2: 10, S3: 10, S4: 25, S5: 35, S6: 5 }
			: { ...BASE_WEIGHTS };
		if (newFilters.family === 'children') { w.S2 += 15; w.S1 += 10; w.S5 -= 10; w.S6 -= 5; }
		else if (newFilters.mobility === 'no_car') { w.S1 += 20; w.S6 += 10; w.S5 -= 20; w.S3 -= 10; }
		if (newFilters.work === 'remote') { w.S6 += 15; w.S3 += 10; w.S5 -= 15; w.S4 -= 10; }
		if (newFilters.pace === 'active') { w.S3 += 10; w.S5 += 10; w.S2 -= 10; w.S6 -= 10; }
		if (newFilters.travel_frequency === 'often') { w.S4 += 15; w.S3 -= 15; }
		if (newFilters.climate_sensitivity === 'high') { w.S6 += 15; w.S4 -= 15; }
		if (newFilters.medicine_threshold_min === 15) { w.S1 += 20; }
		else if (newFilters.medicine_threshold_min === 30) { w.S1 += 10; }
		(Object.keys(w) as Subindex[]).forEach(k => { if (w[k] < 0) w[k] = 0; });
		const sum = Object.values(w).reduce((a, b) => a + b, 0);
		(Object.keys(w) as Subindex[]).forEach(k => { w[k] = Math.round((w[k] / sum) * 100 * 10) / 10; });
		return w;
	};

	const updateFilter = <K extends keyof Filters>(key: K, value: Filters[K]) => {
		const newFilters = { ...f, [key]: value };
		const newWeights = recalculateWeights(newFilters);
		setProfile({ ...profile, preset: null, filters: newFilters, weights: newWeights });
	};

	const custom = profile.preset === null;

	return (
		<>
			<button
				ref={buttonRef}
				onClick={toggleMenu}
				aria-expanded={isOpen}
				className={`relative inline-flex h-10 items-center gap-2 whitespace-nowrap rounded-xl px-3.5 text-[13px] font-bold transition-all duration-300 ease-spring ${
					isOpen ? 'bg-ink-900 text-white shadow-soft' : 'text-ink-700 hover:bg-white hover:text-ink-900 hover:shadow-soft'
				}`}
			>
				<Icon name="sliders" className="h-4 w-4" />
				Настроить
				{custom && <span className="h-2 w-2 rounded-full bg-brand-gradient ring-2 ring-white" title="Свой профиль" />}
			</button>

			{isOpen && createPortal(
				<div
					ref={menuRef}
					style={{ top: coords.top, left: coords.left, width: MENU_WIDTH }}
					className="glass-strong absolute z-[100] max-w-[calc(100vw-16px)] whitespace-normal rounded-3xl p-5 text-sm animate-scale-in"
				>
					<div className="flex items-start justify-between gap-3">
						<div>
							<h3 className="font-display text-base font-semibold tracking-tight text-ink-900">Ваш профиль</h3>
							<p className="mt-0.5 text-xs font-medium text-ink-500">Веса индекса пересчитываются сразу</p>
						</div>
						<button
							onClick={() => setIsOpen(false)}
							aria-label="Закрыть"
							className="grid h-8 w-8 place-items-center rounded-full text-ink-400 transition hover:bg-ink-100 hover:text-ink-900"
						>
							<Icon name="x" className="h-4 w-4" />
						</button>
					</div>

					<WeightsBar weights={profile.weights} />

					<div className="mt-4 max-h-[min(58vh,34rem)] space-y-2.5 overflow-y-auto pr-1 scrollbar-soft">
						{FIELDS.map((field) => (
							<div key={field.key}>
								<div className="mb-1.5 text-xs font-bold text-ink-600">{field.label}</div>
								<Segmented
									size="sm"
									ariaLabel={field.label}
									value={f[field.key]}
									options={field.options}
									onChange={(v) => updateFilter(field.key, v as Filters[typeof field.key])}
								/>
							</div>
						))}
					</div>

					<label className="mt-4 flex cursor-pointer items-center justify-between gap-3 rounded-2xl bg-ink-50 p-3 ring-1 ring-ink-100 transition hover:bg-white">
						<span>
							<span className="block text-[13px] font-bold text-ink-800">Режим риелтора</span>
							<span className="block text-xs text-ink-500">Веса под выбор жилья и бейдж привлекательности</span>
						</span>
						<input
							type="checkbox"
							checked={f.realtor_mode}
							onChange={(e) => updateFilter('realtor_mode', e.target.checked)}
							className="peer sr-only"
						/>
						<span className="relative h-6 w-11 shrink-0 rounded-full bg-ink-200 transition-colors peer-checked:bg-brand-500 peer-focus-visible:ring-4 peer-focus-visible:ring-brand-100 after:absolute after:left-0.5 after:top-0.5 after:h-5 after:w-5 after:rounded-full after:bg-white after:shadow after:transition-transform after:duration-300 peer-checked:after:translate-x-5" />
					</label>
				</div>,
				document.body
			)}
		</>
	);
}

/** How the index is weighted now: one coloured segment per subindex. */
function WeightsBar({ weights }: { weights: Profile['weights'] }) {
	const keys = Object.keys(weights) as Subindex[];
	return (
		<div className="mt-4 rounded-2xl bg-white/70 p-3 ring-1 ring-ink-100">
			<div className="flex h-2.5 overflow-hidden rounded-full bg-ink-100">
				{keys.map((k) => (
					<div
						key={k}
						className="h-full transition-[width] duration-500 ease-spring first:rounded-l-full last:rounded-r-full"
						style={{ width: `${weights[k]}%`, background: WEIGHT_COLORS[k] }}
					/>
				))}
			</div>
			<div className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1.5">
				{keys.map((k) => (
					<div key={k} className="flex items-center gap-1.5 text-[11px] font-semibold text-ink-500">
						<span className="h-2 w-2 shrink-0 rounded-full" style={{ background: WEIGHT_COLORS[k] }} />
						<span className="truncate">{SUBINDEX_LABELS[k]}</span>
						<span className="ml-auto tabular-nums text-ink-700">{Math.round(weights[k])}%</span>
					</div>
				))}
			</div>
		</div>
	);
}
