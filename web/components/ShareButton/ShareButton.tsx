export function ShareButton() {
	const onClick = async () => {
		const url = window.location.href;
		await navigator.clipboard.writeText(url);
		alert(`Ссылка скопирована (${url.length} симв.)`);
	};
	return <button onClick={onClick} className="px-3 py-1 rounded bg-emerald-600 text-white">Поделиться</button>;
}