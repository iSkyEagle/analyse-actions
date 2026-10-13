/**
 * Formulation de la position d'une métrique dans son groupe de comparaison (étape 3b).
 *
 * Purement descriptif : « au-dessus de 89 % des 31 sociétés comparables (logiciels) ».
 * Le jugement — bon ou mauvais selon le sens de la métrique — est porté par les notes (3c),
 * calculées à l'ingestion ; cette phrase reste une position.
 */
import { metrique } from "./format";
import type { LigneMetrique, StatGroupe } from "./queries";

export type Groupes = { industrie: string | null; secteur: string | null; stats: StatGroupe[] };

/** « logiciels », ou « secteur technologie » quand l'industrie était trop petite. */
export function nomGroupe(niveau: "industrie" | "secteur", g: Groupes): string {
  return niveau === "industrie" ? (g.industrie ?? "").toLowerCase() : `secteur ${(g.secteur ?? "").toLowerCase()}`;
}

/**
 * Une métrique ne se calcule pas pour toutes les sociétés du groupe : pas de PER pour
 * une société en perte. Dire « des 70 sociétés comparables » quand l'industrie en compte
 * 113 laisserait croire à une erreur ; on dit lesquelles.
 */
const RESTRICTION: Record<string, string> = {
  per: "bénéficiaires",
  ve_ebitda: "à EBITDA positif",
  ve_fcf: "à flux de trésorerie libre positif",
  dette_nette_ebitda: "à EBITDA positif",
  couverture_interets: "endettées",
  cagr_bpa_3a: "bénéficiaires sur la période",
  cagr_bpa_5a: "bénéficiaires sur la période",
  cagr_fcf_5a: "à flux de trésorerie libre positif sur la période",
};

/** Taille du groupe : la métrique la mieux couverte en donne le nombre de sociétés. */
function tailleGroupe(niveau: "industrie" | "secteur", g: Groupes): number {
  return g.stats.reduce((m, s) => (s.niveau === niveau && s.n > m ? s.n : m), 0);
}

function quiCompare(l: LigneMetrique, g: Groupes): string {
  const niveau = l.percentile_niveau!;
  const groupe = nomGroupe(niveau, g);
  const total = tailleGroupe(niveau, g);
  if (total > 0 && l.percentile_n! < 0.9 * total) {
    const quelles = RESTRICTION[l.metric_id] ?? "pour lesquelles ce chiffre se calcule";
    return `sociétés comparables ${quelles} (${groupe}, ${total} sociétés en tout)`;
  }
  return `sociétés comparables (${groupe})`;
}

/**
 * Phrase de position, ou null si la métrique n'a pas de percentile (non applicable,
 * groupe de moins de 15 sociétés). Le côté le plus parlant est retenu : « au-dessus de
 * 89 % » plutôt que « en dessous de 11 % ».
 */
export function phrasePosition(l: LigneMetrique | undefined, format: string, g: Groupes): string | null {
  if (!l || l.percentile === null || !l.percentile_niveau || !l.percentile_n) return null;
  const p = l.percentile;
  const qui = `des ${l.percentile_n} ${quiCompare(l, g)}`;
  const position = p >= 50 ? `Au-dessus de ${Math.min(p, 99)} % ${qui}` : `En dessous de ${Math.min(100 - p, 99)} % ${qui}`;
  const st = g.stats.find((s) => s.niveau === l.percentile_niveau && s.metric_id === l.metric_id);
  return st?.mediane != null ? `${position}, dont la médiane est de ${metrique(st.mediane, format)}.` : `${position}.`;
}
