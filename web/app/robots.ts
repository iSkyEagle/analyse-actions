import type { MetadataRoute } from "next";

/** Outil privé : aucun moteur de recherche n'est invité (étape 6). */
export default function robots(): MetadataRoute.Robots {
  return { rules: { userAgent: "*", disallow: "/" } };
}
