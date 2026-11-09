'use client';

import Link from 'next/link';
import { CompareView } from '@/components/Compare/CompareView';

export default function ComparePage() {
	return (
		<main className="min-h-screen bg-gray-50">
			<header className="bg-white p-4 shadow-sm mb-6 flex justify-between items-center">
				<h1 className="text-xl font-bold text-gray-800">Сравнение территорий</h1>
				<Link href="/" className="text-sm font-medium text-blue-600 hover:underline">← Назад к карте</Link>
			</header>
			
			<div className="max-w-5xl mx-auto bg-white rounded-xl shadow p-6">
				<CompareView ids={[42, 43, 44]} />
			</div>
		</main>
	);
}