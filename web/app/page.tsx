'use client';

import { useCallback, useState } from 'react';
import { AtlasMap } from '@/components/Map/AtlasMap';
import { TerritoryCard } from '@/components/Map/TerritoryCard';
import { ProfilePicker } from '@/components/Profile/ProfilePicker';
import { ShareButton } from '@/components/ShareButton/ShareButton';
import { ChatWidget } from '@/components/Chat/ChatWidget';
import { Sidebar } from '@/components/Nav/Sidebar';
import { Brand } from '@/components/UI/Brand';
import { Icon } from '@/components/UI/Icon';

export default function Home() {
	const [isSidebarOpen, setIsSidebarOpen] = useState(false);
	const closeSidebar = useCallback(() => setIsSidebarOpen(false), []);

	return (
		<main className="relative h-dvh w-screen overflow-hidden bg-ink-100">
			<AtlasMap />
			<TerritoryCard />

			<header className="pointer-events-none absolute inset-x-3 top-3 z-30 flex flex-col items-start gap-2 md:inset-x-4 md:top-4 md:flex-row md:items-center">
				<div className="glass pointer-events-auto flex shrink-0 items-center gap-1 rounded-2xl p-1.5 pr-4 animate-fade-up">
					<button
						onClick={() => setIsSidebarOpen(true)}
						aria-label="Открыть меню"
						className="grid h-10 w-10 place-items-center rounded-xl text-ink-600 transition hover:bg-white hover:text-ink-900 hover:shadow-soft"
					>
						<Icon name="menu" className="h-5 w-5" />
					</button>
					<Brand />
				</div>

				<div className="glass pointer-events-auto min-w-0 max-w-full overflow-x-auto rounded-2xl p-1.5 scrollbar-hide animate-fade-up [animation-delay:90ms]">
					<ProfilePicker />
				</div>
			</header>

			<Sidebar isOpen={isSidebarOpen} onClose={closeSidebar} />

			<ShareButton />
			<ChatWidget />
		</main>
	);
}
