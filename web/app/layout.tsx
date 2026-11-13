import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { ProfileBootstrapper } from "@/components/ProfileBootstrapper";
import { Providers } from "@/app/providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin", "cyrillic"] });

export const metadata: Metadata = {
	title: "Pulse Score",
	description: "Индекс транспортной связанности",
};

export default function RootLayout({
  	children,
}: Readonly<{
  	children: React.ReactNode;
}>) {
	return (
		<html lang="ru">
			<body className={inter.className}>
				<ProfileBootstrapper />
				<Providers>
					{children}
				</Providers>
			</body>
		</html>
	);
}