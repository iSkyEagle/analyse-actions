import type { Metadata, Viewport } from "next";
import ServiceWorker from "./composants/ServiceWorker";
import EnTete from "./composants/EnTete";
import { Public_Sans } from "next/font/google";
import "./globals.css";

// Public Sans : le caractère du système de design du gouvernement fédéral américain.
// Les documents que l'outil lit sont des dépôts fédéraux ; ses chiffres tabulaires
// alignent les colonnes de montants.
const publicSans = Public_Sans({ subsets: ["latin"], variable: "--font-public-sans", display: "swap" });

export const metadata: Metadata = {
  title: "Analyse d'actions",
  description: "Lecture argumentée des comptes des sociétés cotées aux États-Unis.",
  // Outil privé : jamais indexé, même si une adresse venait à circuler (étape 6).
  robots: { index: false, follow: false },
  // Application installée sur iPhone : plein écran, barre d'état sur le papier.
  appleWebApp: { capable: true, title: "Analyse", statusBarStyle: "default" },
};

// Couleur de la barre du navigateur et de l'application installée : le papier, en
// clair comme en sombre.
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#F6F7F4" },
    { media: "(prefers-color-scheme: dark)", color: "#141C22" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr" className={publicSans.variable}>
      <body className="min-h-screen font-sans antialiased">
        <EnTete />
        {children}
        <ServiceWorker />
      </body>
    </html>
  );
}
