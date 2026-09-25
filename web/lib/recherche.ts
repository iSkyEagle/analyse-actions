/**
 * lib/recherche.ts — Recherche de valeurs, exécutée dans le navigateur.
 *
 * La liste complète des sociétés (~4 800 lignes, ~50 Ko compressés) est chargée une
 * fois puis filtrée en mémoire. Une requête SQL par frappe aurait coûté une connexion
 * au pooler à chaque touche, sur une instance nano qu'on a déjà vue saturer ; ici la
 * base n'est pas sollicitée pendant la saisie, et la recherche fonctionne hors ligne.
 *
 * Module pur, sans dépendance : il est testé isolément et partagé entre le composant
 * client et les tests.
 */

/** Ligne compacte telle que servie par /api/societes : [ticker, nom, cik, univers, liquidité]. */
export type LigneBrute = [string, string, number, 0 | 1, number];

export type Entree = {
  ticker: string;
  nom: string;
  cik: number;
  univers: boolean;
  liquidite: number;   // log10 du volume médian en dollars, 0 si inconnu
  tickerNorm: string;
  mots: string[];
  nomNorm: string;
  soeurs: string[];    // autres lignes de cotation du même émetteur, calculées une fois
};

export type Resultat = {
  ticker: string;
  nom: string;
  univers: boolean;
  autres: string[];    // autres lignes de cotation du même émetteur
};

/**
 * Mots de raison sociale qui n'identifient rien. Sans ce filtre, taper « corp » ou
 * « holdings » ferait remonter des centaines de sociétés au même rang.
 */
const MOTS_VIDES = new Set([
  "INC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "LLC", "LP",
  "SA", "NV", "AG", "SE", "THE", "HOLDINGS", "HOLDING", "GROUP", "TRUST", "CLASS",
]);

/** Majuscules, sans accents, ponctuation remplacée par des espaces. */
export function normaliser(s: string): string {
  return s
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, " ")
    .trim();
}

/** « BRK.B », « BRK-B », « brk b » -> « BRKB » : la ponctuation d'un ticker varie selon la source. */
export function normaliserTicker(s: string): string {
  return s.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export function indexer(lignes: LigneBrute[]): Entree[] {
  // Regroupement par émetteur calculé UNE fois ici, et non à chaque frappe.
  const parCik = new Map<number, string[]>();
  for (const [ticker, , cik] of lignes) {
    const l = parCik.get(cik);
    if (l) l.push(ticker);
    else parCik.set(cik, [ticker]);
  }
  return lignes.map(([ticker, nom, cik, univers, liquidite]) => {
    const nomNorm = normaliser(nom);
    return {
      ticker, nom, cik, univers: univers === 1, liquidite,
      tickerNorm: normaliserTicker(ticker),
      mots: nomNorm.split(" ").filter((m) => m && !MOTS_VIDES.has(m)),
      nomNorm,
      soeurs: (parCik.get(cik) ?? []).filter((t) => t !== ticker),
    };
  });
}

/**
 * Pertinence d'une entrée pour une requête, 0 si elle ne correspond pas.
 *
 * Paliers, du plus fort au plus faible :
 *   ticker exact > début de ticker > tous les mots au début d'un mot du nom
 *   > premier mot du nom > sous-chaîne du nom
 * puis, à palier égal : les valeurs de l'univers suivi, puis les plus liquides. C'est
 * ce dernier critère qui fait sortir Apple avant une micro-capitalisation homonyme.
 */
export function score(e: Entree, requete: string): number {
  const q = normaliser(requete);
  if (!q) return 0;
  const jetons = q.split(" ");
  const qt = normaliserTicker(requete);
  // Un ticker se tape sans espace, mais souvent avec ponctuation : « brk.b ». Le
  // critère est l'absence d'espace dans la saisie BRUTE — la normalisation, elle,
  // change le point en espace et faisait croire à deux mots.
  const formeTicker = !/\s/.test(requete.trim());

  let base = 0;
  if (formeTicker && e.tickerNorm === qt) base = 1000;
  else if (formeTicker && qt.length >= 1 && e.tickerNorm.startsWith(qt)) {
    base = 800 - (e.tickerNorm.length - qt.length) * 10;
  } else if (jetons.every((j) => e.mots.some((m) => m.startsWith(j)))) {
    base = e.mots[0]?.startsWith(jetons[0]) ? 600 : 500;
  } else if (q.length >= 3 && e.nomNorm.includes(q)) {
    base = 200;
  }
  if (base === 0) return 0;
  return base + (e.univers ? 40 : 0) + Math.min(e.liquidite, 12);
}

/**
 * Les meilleurs résultats, un par émetteur.
 *
 * Deux lignes de cotation d'un même émetteur — GOOGL et GOOG, BRKR et BRKRP —
 * partagent un seul jeu de comptes : les afficher côte à côte en doublon induirait
 * en erreur. On garde la mieux classée et on cite les autres.
 */
export function rechercher(index: Entree[], requete: string, max = 8): Resultat[] {
  const notes: { e: Entree; s: number }[] = [];
  for (const e of index) {
    const s = score(e, requete);
    if (s > 0) notes.push({ e, s });
  }
  notes.sort((a, b) => b.s - a.s || a.e.ticker.localeCompare(b.e.ticker));

  const vus = new Set<number>();
  const out: Resultat[] = [];
  for (const { e } of notes) {
    if (vus.has(e.cik)) continue;
    vus.add(e.cik);
    out.push({ ticker: e.ticker, nom: e.nom, univers: e.univers, autres: e.soeurs });
    if (out.length >= max) break;
  }
  return out;
}
