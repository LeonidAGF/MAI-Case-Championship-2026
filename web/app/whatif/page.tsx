'use client';

import { WhatIfPanel } from '@/components/WhatIf/WhatIfPanel';
import { PageHeader } from '@/components/UI/PageHeader';

export default function WhatIfPage() {
	return (
		<main className="min-h-dvh bg-mesh pb-16">
			<PageHeader title="Симулятор инфраструктуры" subtitle="Что будет с индексом, если открыть объект или улучшить дорогу" icon="flask" />

			<div className="mx-auto mt-6 max-w-6xl px-3 md:mt-8 md:px-6">
				<WhatIfPanel />
			</div>
		</main>
	);
}
