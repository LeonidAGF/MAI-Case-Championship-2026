'use client';

import Link from 'next/link';
import { WhatIfPanel } from '@/components/WhatIf/WhatIfPanel';

export default function WhatIfPage() {
	return (
		<main className="min-h-screen bg-gray-50 pb-12">
			<header className="bg-white p-3 md:p-4 shadow-sm mb-4 md:mb-6 flex justify-between items-center sticky top-0 z-10">
				<h1 className="text-base md:text-xl font-bold text-gray-800 truncate pr-2">Симулятор инфраструктуры</h1>
				<Link href="/" className="text-xs md:text-sm font-medium text-blue-600 hover:underline shrink-0">← Назад</Link>
			</header>
			
			<div className="max-w-4xl mx-auto px-3 md:px-4">
				<div className="bg-white rounded-xl shadow p-3 md:p-6">
					<WhatIfPanel />
				</div>
			</div>
		</main>
	);
}