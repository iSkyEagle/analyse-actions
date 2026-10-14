/**
 * Lecture des notes (étape 3c) : profil en mots, note sur 10. Partagée par la synthèse
 * de la fiche et par la comparaison (étape 5). Les mots et les seuils viennent de
 * metriques.json, exporté de metriques.py : rien n'est jugé ici.
 */
import config from "@/lib/metriques.json";
import type { Note } from "@/lib/queries";

export type Axe = "valorisation" | "qualite" | "croissance" | "dilution";

export const ORDRE_PROFIL: Axe[] = ["qualite", "valorisation", "croissance", "dilution"];
const MOTS = config.profil_mots as Record<Axe, [string, string, string]>;
const [SEUIL_BAS, SEUIL_HAUT] = config.seuils_mots as [number, number];

export const majuscule = (t: string) => t.replace(/^./, (c) => c.toUpperCase());
export const surDix = (score: number) => Math.round(score / 10);

/** Mots du profil, sur la note précise (sur 100) : 6,4 reste « dans la moyenne ». */
export function motProfil(axe: Axe, score: number): string {
  const n = score / 10;
  return MOTS[axe][n < SEUIL_BAS ? 0 : n >= SEUIL_HAUT ? 2 : 1];
}

/** « Fondamentaux solides, valorisation dans la moyenne… », sur les seuls axes notés. */
export function profilEnMots(notes: Note[]): string | null {
  const mots = ORDRE_PROFIL.flatMap((a) => {
    const n = notes.find((x) => x.axis === a);
    return n?.score != null ? [motProfil(a, n.score)] : [];
  });
  return mots.length ? majuscule(mots.join(", ")) + "." : null;
}

/** Verdict de valorisation en quelques mots, pour les listes (accueil, étape 6). */
export const VERDICT_COURT: Record<string, string> = {
  parmi_plus_bas: "parmi ses plus basses sur 5 ans",
  moins_cher: "moins chère que d'habitude",
  moyenne: "dans sa moyenne",
  plus_cher: "plus chère que d'habitude",
  parmi_plus_hauts: "parmi ses plus hautes sur 5 ans",
};
