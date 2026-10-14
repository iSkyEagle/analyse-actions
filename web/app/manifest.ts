import type { MetadataRoute } from "next";

/**
 * Application installable (étape 6) : nom, icônes et couleurs du registre comptable.
 * `standalone` : sans barre de navigateur — d'où l'en-tête commun avec un retour.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Analyse d'actions",
    short_name: "Analyse",
    description: "Les comptes des sociétés cotées aux États-Unis, notés face à leurs comparables.",
    lang: "fr",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#F6F7F4",
    theme_color: "#F6F7F4",
    icons: [
      { src: "/icone-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icone-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/icone-masquable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
