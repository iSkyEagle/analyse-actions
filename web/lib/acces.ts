/**
 * Accès par mot de passe (étape 6, décidé le 14 octobre 2026).
 *
 * Un seul mot de passe partagé, dans la variable d'environnement ACCES_MOT_DE_PASSE.
 * Absente — en local, ou si l'on rouvre un jour le site — : aucune protection.
 *
 * Le cookie ne contient pas le mot de passe mais une empreinte HMAC qui en dépend :
 * changer le mot de passe sur Vercel déconnecte tous les appareils d'un coup.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

export const COOKIE_ACCES = "acces";
export const DUREE_ACCES = 90 * 24 * 3600;          // secondes : 90 jours

export const motDePasse = () => process.env.ACCES_MOT_DE_PASSE || null;

export function jeton(mdp: string): string {
  return createHmac("sha256", mdp).update("analyse-actions/acces/v1").digest("hex");
}

/** Comparaison à temps constant : la durée ne trahit pas la longueur commune. */
export function egal(a: string, b: string): boolean {
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** Destination après connexion : un chemin interne, jamais une autre origine. */
export function suiteSure(suite: string | null | undefined): string {
  return suite && suite.startsWith("/") && !suite.startsWith("//") && !suite.startsWith("/\\") ? suite : "/";
}
