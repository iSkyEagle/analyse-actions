import type { Metadata } from "next";
import { Public_Sans } from "next/font/google";
import "./globals.css";

// Public Sans : le caractère du système de design du gouvernement fédéral américain.
// Les documents que l'outil lit sont des dépôts fédéraux ; ses chiffres tabulaires
// alignent les colonnes de montants.
const publicSans = Public_Sans({ subsets: ["latin"], variable: "--font-public-sans", display: "swap" });

export const metadata: Metadata = {
  title: "Analyse d'actions",
  description: "Lecture argumentée des comptes des sociétés cotées aux États-Unis.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" className={publicSans.variable}>
      <body className="min-h-screen font-sans antialiased">{children}</body>
    </html>
  );
}
