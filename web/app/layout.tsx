import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Analyse d'actions",
  description: "Lecture argumentée des fondamentaux des actions cotées aux États-Unis.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
