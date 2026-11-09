// apps/web/app/page.tsx
'use client';

import { AtlasMap } from '@/components/Map/AtlasMap';
import { TerritoryCard } from '@/components/Map/TerritoryCard';
import { ProfilePicker } from '@/components/Profile/ProfilePicker';
import { ShareButton } from '@/components/ShareButton/ShareButton';
import Link from 'next/link';

export default function Home() {
	return (
		<main className="relative w-screen h-screen overflow-hidden bg-gray-100">
			<AtlasMap />
			<TerritoryCard />
			
			<header className="absolute top-4 left-4 right-4 flex justify-between items-start z-10 pointer-events-none">
				
				<div className="flex gap-2 pointer-events-auto bg-white/90 backdrop-blur p-2 rounded-xl shadow-lg border border-gray-100">
					<ProfilePicker />
					<div className="w-px bg-gray-200 mx-1"></div>
					<ShareButton />
				</div>

				<nav className="flex gap-4 bg-white/90 backdrop-blur p-3 px-6 rounded-full shadow-lg border border-gray-100 font-medium text-sm pointer-events-auto">
					<span className="text-blue-600 border-b-2 border-blue-600 pb-1 cursor-pointer">Карта</span>
					<Link href="/compare" className="text-gray-500 hover:text-black cursor-pointer transition">Сравнить</Link>
					<Link href="/whatif" className="text-gray-500 hover:text-black cursor-pointer transition">Что если</Link>
				</nav>
			</header>
		</main>
	);
}