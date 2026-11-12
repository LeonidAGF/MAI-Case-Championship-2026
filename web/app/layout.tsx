import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { ProfileBootstrapper } from "@/components/ProfileBootstrapper";
import "./globals.css";

const inter = Inter({ subsets: ["latin", "cyrillic"] });

export const metadata: Metadata = {
	title: "Атлас доступности СКФО",
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
				{/* Restores the profile from ?p= or localStorage after hydration (shared links work). */}
				<ProfileBootstrapper />
				{children}
			</body>
		</html>
	);
}