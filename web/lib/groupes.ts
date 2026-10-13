/**
 * Formulation de la position d'une métrique dans son groupe de comparaison (étape 3b).
 *
 * Purement descriptif : « au-dessus de 89 % des 31 sociétés comparables (logiciels) ».
 * Le jugement — bon ou mauvais selon le sens de la métrique — viendra avec les notes (3c).
 */
import { metrique } from "./format";
import type { LigneMetrique, StatGroupe } from "./queries";

export type Groupes = { industrie: string | null; secteur: string | null; stats: StatGroupe[] };

/** « logiciels », ou « secteur technologie » quand l'industrie était trop petite. */
export function nomGroupe(niveau: "industrie" | "secteur", g: Groupes): string {
  return niveau === "industrie" ? (g.industrie ?? "").toLowerCase() : `secteur ${(g.secteur ?? "").toLowerCase()}`;
}

/**
 * Phrase de position, ou null si la métrique n'a pas de percentile (non applicable,
 * groupe de moins de 15 sociétés). Le côté le plus parlant est retenu : « au-dessus de
 * 89 % » plutôt que « en dessous de 11 % ».
 */
export function phrasePosition(l: LigneMetrique | undefined, format: string, g: Groupes): string | null {
  if (!l || l.percentile === null || !l.percentile_niveau || !l.percentile_n) return null;
  const p = l.percentile;
  const qui = `des ${l.percentile_n} sociétés comparables (${nomGroupe(l.percentile_niveau, g)})`;
  const position = p >= 50 ? `Au-dessus de ${Math.min(p, 99)} % ${qui}` : `En dessous de ${Math.min(100 - p, 99)} % ${qui}`;
  const st = g.stats.find((s) => s.niveau === l.percentile_niveau && s.metric_id === l.metric_id);
  return st?.mediane != null ? `${position}, dont la médiane est de ${metrique(st.mediane, format)}.` : `${position}.`;
}
