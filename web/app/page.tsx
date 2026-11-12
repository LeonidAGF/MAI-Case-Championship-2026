'use client';

import { AtlasMap } from '@/components/Map/AtlasMap';
import { TerritoryCard } from '@/components/Map/TerritoryCard';
import { ProfilePicker } from '@/components/Profile/ProfilePicker';
import { ShareButton } from '@/components/ShareButton/ShareButton';
import { ChatWidget } from '@/components/Chat/ChatWidget';
import Link from 'next/link';

export default function Home() {
	return (
		<main className="relative w-screen h-screen overflow-hidden bg-gray-100">
			<AtlasMap />
			<TerritoryCard />
		
			<header className="absolute top-2 md:top-4 left-2 md:left-4 right-2 md:right-4 flex flex-col md:flex-row justify-between items-start gap-2 z-50 pointer-events-none">
				
				<div className="relative z-50 flex flex-wrap items-center gap-2 pointer-events-auto bg-white/95 backdrop-blur p-1.5 md:p-2 rounded-xl shadow-lg border border-gray-100 max-w-full">
					<ProfilePicker />
					<div className="w-px h-6 bg-gray-200 mx-1 shrink-0"></div>
					<ShareButton />
				</div>

				<nav className="relative z-10 self-end md:self-auto flex gap-4 bg-white/95 backdrop-blur p-2 md:p-3 px-4 md:px-6 rounded-full shadow-lg border border-gray-100 font-medium text-xs md:text-sm pointer-events-auto shrink-0">
					<span className="text-blue-600 border-b-2 border-blue-600 pb-0.5 cursor-pointer">Карта</span>
					<Link href="/compare" className="text-gray-500 hover:text-black cursor-pointer transition">Сравнить</Link>
					<Link href="/whatif" className="text-gray-500 hover:text-black cursor-pointer transition">Что если</Link>
				</nav>
			</header>

			<ChatWidget />
		</main>
	);
}