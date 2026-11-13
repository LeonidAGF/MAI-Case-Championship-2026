import type { Metadata, Viewport } from 'next';
// Bundled variable fonts (latin + cyrillic): nothing is loaded from other servers at runtime.
import '@fontsource-variable/manrope';
import '@fontsource-variable/unbounded';
import { ProfileBootstrapper } from '@/components/ProfileBootstrapper';
import { Providers } from '@/app/providers';
import './globals.css';

export const metadata: Metadata = {
	title: 'Pulse Score',
	description: 'Индекс транспортной связанности',
};

export const viewport: Viewport = {
	themeColor: '#f6f7fb',
};

export default function RootLayout({
	children,
}: Readonly<{
	children: React.ReactNode;
}>) {
	return (
		<html lang="ru">
			<body className="font-sans antialiased">
				<ProfileBootstrapper />
				<Providers>
					{children}
				</Providers>
			</body>
		</html>
	);
}
