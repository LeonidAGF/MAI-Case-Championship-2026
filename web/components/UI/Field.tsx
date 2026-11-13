import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from 'react';
import { Icon } from '@/components/UI/Icon';

export const inputClass =
	'h-11 w-full rounded-xl border border-ink-200 bg-white/90 px-3.5 text-sm font-medium text-ink-900 shadow-[inset_0_1px_2px_rgb(23_27_53/0.04)] outline-none transition placeholder:font-normal placeholder:text-ink-400 hover:border-ink-300 focus:border-brand-400 focus:ring-4 focus:ring-brand-100 disabled:opacity-60';

export function FieldLabel({ children, htmlFor, hint }: { children: ReactNode; htmlFor?: string; hint?: ReactNode }) {
	return (
		<label htmlFor={htmlFor} className="mb-1.5 flex items-baseline justify-between gap-2 text-xs font-bold text-ink-600">
			<span>{children}</span>
			{hint && <span className="font-medium text-ink-400">{hint}</span>}
		</label>
	);
}

export function TextInput({ className = '', ...props }: InputHTMLAttributes<HTMLInputElement>) {
	return <input {...props} className={`${inputClass} ${className}`} />;
}

/** Native select (keyboard and screen readers keep working) with the house look and a chevron. */
export function Select({ className = '', children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
	return (
		<div className={`relative ${className}`}>
			<select {...props} className={`${inputClass} cursor-pointer appearance-none pr-10`}>
				{children}
			</select>
			<Icon name="chevron-down" className="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400" />
		</div>
	);
}
