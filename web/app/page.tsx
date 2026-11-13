'use client';

import { useState } from 'react';
import { AtlasMap } from '@/components/Map/AtlasMap';
import { TerritoryCard } from '@/components/Map/TerritoryCard';
import { ProfilePicker } from '@/components/Profile/ProfilePicker';
import { ShareButton } from '@/components/ShareButton/ShareButton';
import { ChatWidget } from '@/components/Chat/ChatWidget';
import Link from 'next/link';

function Sidebar({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
	return (
		<>
			{isOpen && (
				<div 
					className="fixed inset-0 bg-black/20 z-40 transition-opacity"
					onClick={onClose}
				/>
			)}

			<aside className={`fixed top-0 left-0 h-full w-64 bg-white shadow-2xl z-50 transform transition-transform duration-300 ease-in-out flex flex-col ${isOpen ? 'translate-x-0' : '-translate-x-full'}`}>
				<div className="p-4 border-b flex justify-between items-center bg-gray-50">
					<h2 className="font-bold text-gray-800">Навигация</h2>
					<button onClick={onClose} className="p-1 hover:bg-gray-200 rounded text-gray-500">
						✕
					</button>
				</div>
				
				<nav className="flex flex-col p-4 gap-2">
					<Link href="/" className="p-3 rounded-lg hover:bg-blue-50 text-blue-600 font-medium">Карта</Link>
					<Link href="/climate" className="p-3 rounded-lg hover:bg-gray-100 text-gray-700 font-medium">Климат</Link>
					<Link href="/compare" className="p-3 rounded-lg hover:bg-gray-100 text-gray-700 font-medium">Сравнить</Link>
					<Link href="/whatif" className="p-3 rounded-lg hover:bg-gray-100 text-gray-700 font-medium">Что если</Link>
					<Link href="https://github.com/LeonidAGF/MAI-Case-Championship-2026" className="p-3 rounded-lg hover:bg-gray-100 text-gray-700 font-medium">API</Link>
					<Link href="https://cson.site:3001" target="_blank" rel="noopener noreferrer" className="p-3 rounded-lg hover:bg-gray-100 text-gray-700 font-medium">Grafana</Link>
				</nav>
			</aside>
		</>
	);
}

export default function Home() {
	const [isSidebarOpen, setIsSidebarOpen] = useState(false);

	return (
		<main className="relative w-screen h-screen overflow-hidden bg-gray-100">
			<AtlasMap />
			<TerritoryCard />
		
			<header className="absolute top-2 md:top-4 left-2 md:left-4 right-2 md:right-4 flex flex-col gap-2 z-30 pointer-events-none">
				
				<div className="pointer-events-auto bg-white/95 backdrop-blur p-1.5 md:p-2 rounded-xl shadow-lg border border-gray-100 w-fit max-w-full md:max-w-[calc(100vw-2rem)] flex flex-nowrap items-center gap-2 overflow-x-auto scrollbar-hide">
					<ProfilePicker />
				</div>

				<button 
					onClick={() => setIsSidebarOpen(true)}
					className="pointer-events-auto self-start flex items-center gap-2 bg-white/95 backdrop-blur px-4 py-2 rounded-xl shadow-lg border border-gray-100 text-sm font-medium text-gray-700 hover:bg-gray-50 transition"
				>
					<svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" /></svg>
					Меню
				</button>
			</header>

			<Sidebar isOpen={isSidebarOpen} onClose={() => setIsSidebarOpen(false)} />

			<ShareButton />
			<ChatWidget />
		</main>
	);
}