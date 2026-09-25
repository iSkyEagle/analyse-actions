/**
 * lib/queries.ts — Lectures de la fiche valeur.
 *
 * Tout le SQL de l'interface vit ici, jamais dans les pages. Les pages reçoivent des
 * objets typés et ne savent pas d'où ils viennent : c'est ce qui permettra, en
 * phase 3, de basculer une lecture vers la table `metrics` précalculée sans toucher
 * à l'affichage.
 */
import "server-only";
import { query } from "./db";

export type Societe = {
  company_id: number;
  history_years: number;
  cik: number;
  ticker: string;
  name: string;
  exchange: string;
  sector: string | null;
  mapping_profile: string;
  market_cap: number | null;
  market_cap_as_of: string | null;
  market_cap_uncertain: boolean;
  data_quality_score: number | null;
  fiscal_years_available: number | null;
  in_universe: boolean;
  prices_refreshed_at: string | null;
  fundamentals_refreshed_at: string | null;
  last_xbrl_filing: string | null;
};

export type Exercice = {
  period_end: string;
  fiscal_year: number | null;
  revenue: number | null;
  net_income: number | null;
  eps_diluted: number | null;
  shares_diluted: number | null;
  fcf: number | null;
  accn: string | null;
  filed: string | null;
  inferred_zero: string[];
  scale_corrected: string[] | null;
};

/**
 * Recherche d'une société par ticker, tolérante à la ponctuation.
 *
 * Correspondance exacte d'abord, puis sur la forme normalisée : « BRK.B », « brk-b »
 * et « BRKB » mènent tous à BRK-B. La ponctuation d'un ticker varie selon la source
 * — point chez les courtiers, tiret chez la SEC —, et le repli sans JavaScript
 * n'avait pas cette tolérance : /v?t=brk.b aboutissait à une page introuvable.
 * L'appelant compare le ticker renvoyé à celui demandé pour rediriger vers l'URL
 * canonique.
 */
export async function societe(ticker: string): Promise<Societe | null> {
  const lignes = await query<Societe>(
    `select company_id, history_years, cik, ticker, name, exchange, sector,
            mapping_profile::text as mapping_profile,
            market_cap, market_cap_as_of, market_cap_uncertain,
            data_quality_score, fiscal_years_available, in_universe,
            prices_refreshed_at, fundamentals_refreshed_at, last_xbrl_filing
     from companies
     where ticker = upper($1)
        or regexp_replace(ticker, '[^A-Z0-9]', '', 'g')
           = regexp_replace(upper($1), '[^A-Z0-9]', '', 'g')
     order by (ticker = upper($1)) desc, in_universe desc
     limit 1`,
    [ticker],
  );
  return lignes[0] ?? null;
}

/**
 * Les exercices annuels les plus récents.
 *
 * Clé par CIK et non par ticker : deux lignes de cotation d'un même émetteur
 * (GOOGL/GOOG) partagent un seul jeu de comptes.
 */
export async function exercices(cik: number, n = 5): Promise<Exercice[]> {
  return query<Exercice>(
    `select period_end, fiscal_year, revenue, net_income, eps_diluted,
            shares_diluted, fcf, accn, filed, inferred_zero, scale_corrected
     from fundamentals
     where cik = $1 and fiscal_period = 'FY'
     order by period_end desc
     limit $2`,
    [cik, n],
  );
}

export type LigneMetrique = { metric_id: string; value: number | null; raison: string | null; as_of: string };
export type PointSerie = { metric_id: string; period_end: string; value: number };

/**
 * Instantané des métriques d'une société, calculé à l'ingestion par metriques.py.
 * L'interface ne calcule rien : elle lit, et affiche la raison quand la valeur manque.
 */
export async function metriques(companyId: number): Promise<LigneMetrique[]> {
  return query<LigneMetrique>(
    `select metric_id, value, raison, as_of from metrics where company_id = $1`,
    [companyId],
  );
}

/** Ratios par exercice, pour la tendance des marges et de la rentabilité. */
export async function series(companyId: number): Promise<PointSerie[]> {
  return query<PointSerie>(
    `select metric_id, period_end, value from metric_series
     where company_id = $1 order by metric_id, period_end`,
    [companyId],
  );
}

/** Point de cours : [date 'AAAA-MM-JJ', clôture ajustée, volume en titres]. */
export type PointCours = [string, number, number];

/**
 * Série complète des cours d'une ligne de cotation, déroulée depuis les tableaux
 * mensuels. Un seul parcours d'index sur la clé primaire (company_id, ym) : mesuré à
 * 12 blocs lus et 0,4 ms pour dix ans en phase 1.
 *
 * Deux arrondis faits en SQL, et non en JavaScript :
 *   - la clôture est stockée en `real` (4 octets) : relue telle quelle, 512,35 devient
 *     512.3499755859375, et la charge utile triple pour du bruit de représentation ;
 *   - `round()` renvoie un `numeric`, que pg transmet en CHAÎNE pour ne rien perdre en
 *     précision — d'où le retour en float8, qui arrive en nombre.
 */
export async function cours(companyId: number): Promise<PointCours[]> {
  const lignes = await query<{ t: string; c: number; v: number }>(
    `select (p.ym + (x.jour - 1))::date as t,
            round(x.cloture::numeric, 4)::float8 as c,
            round(x.volume::numeric)::float8 as v
     from prices p, unnest(p.d, p.c, p.v) as x(jour, cloture, volume)
     where p.company_id = $1
     order by 1`,
    [companyId],
  );
  return lignes.map((r) => [r.t, r.c, r.v]);
}

/**
 * Liste compacte de toutes les valeurs, pour la recherche côté client.
 *
 * TOUTES les sociétés, y compris hors univers et hors périmètre : le mémoire exige
 * que toute valeur ingérée reste accessible par recherche directe de son ticker. Le
 * classement fait remonter l'univers suivi, il ne masque rien.
 *
 * Format tabulaire [ticker, nom, cik, univers, liquidité] plutôt qu'un tableau
 * d'objets : les noms de clés répétés 4 800 fois pèseraient plus que les données.
 */
export async function listeSocietes(): Promise<[string, string, number, 0 | 1, number][]> {
  const lignes = await query<{ t: string; n: string; c: number; u: boolean; l: number | null }>(
    `select ticker as t, name as n, cik as c, in_universe as u,
            round(log(greatest(coalesce(median_dollar_volume_3m, 1), 1))::numeric, 1)::float8 as l
     from companies
     order by ticker`,
  );
  return lignes.map((r) => [r.t, r.n, r.c, r.u ? 1 : 0, r.l ?? 0]);
}

/**
 * Lien vers le dépôt EDGAR d'origine — garde-fou de traçabilité du mémoire : de
 * toute valeur affichée, on doit pouvoir remonter au document source.
 */
export function lienEdgar(cik: number, accn: string | null): string | null {
  if (!accn) return null;
  return `https://www.sec.gov/Archives/edgar/data/${cik}/${accn.replace(/-/g, "")}/`;
}
