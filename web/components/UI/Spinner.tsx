export function Spinner({ className = 'h-5 w-5 text-brand-500' }: { className?: string }) {
	return (
		<span
			aria-hidden="true"
			className={`inline-block shrink-0 animate-spin rounded-full border-2 border-current border-r-transparent ${className}`}
		/>
	);
}
