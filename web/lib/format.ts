/** lib/format.ts — Mise en forme des nombres et des dates, en français. */

const nf = (max: number) =>
  new Intl.NumberFormat("fr-FR", { maximumFractionDigits: max, minimumFractionDigits: 0 });

/**
 * Décimales FIXES. Dans une colonne de chiffres, « 55,68 » au-dessus de « 46,5 » casse
 * l'alignement que les chiffres tabulaires sont justement censés garantir : le nombre
 * de décimales dépend de l'ordre de grandeur, jamais de la valeur particulière.
 */
const nfx = (d: number) =>
  new Intl.NumberFormat("fr-FR", { maximumFractionDigits: d, minimumFractionDigits: d });

/** Trois chiffres significatifs environ : 0 décimale dès 100, 1 dès 10, 2 en dessous. */
const precision = (a: number) => (a >= 100 ? 0 : a >= 10 ? 1 : 2);

/**
 * Montant en dollars, ramené à l'échelle lisible : 3 725 Md$, 3,71 Md$, 142 M$.
 *
 * Jamais de « Bn$ » au-delà du millier de milliards : en français un billion vaut mille
 * milliards, en anglais un milliard — l'abréviation se lirait avec un facteur mille
 * d'écart selon le lecteur. Le milliard reste l'unité, quitte à dépasser mille.
 */
export function dollars(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `${nfx(precision(a / 1e9)).format(v / 1e9)} Md$`;
  if (a >= 1e6) return `${nfx(precision(a / 1e6)).format(v / 1e6)} M$`;
  if (a >= 1e3) return `${nfx(0).format(v / 1e3)} k$`;
  return `${nfx(2).format(v)} $`;
}

export function nombre(v: number | null | undefined, decimales = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return nfx(decimales).format(v);
}

/** Nombre d'actions en millions : sans décimale au-delà de mille millions. */
export function actions(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const m = v / 1e6;
  return `${nfx(Math.abs(m) >= 1000 ? 0 : 1).format(m)} M`;
}

/** 'AAAA-MM-JJ' -> '31 mars 2026'. La date arrive en chaîne, jamais en objet Date. */
export function date(v: string | null | undefined): string {
  if (!v) return "—";
  const [a, m, j] = v.split("-").map(Number);
  return new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(a, m - 1, j)));
}

/** Remplace les dates ISO d'un texte par leur forme française : « au 2026-04-03 » -> « au 3 avril 2026 ». */
export function datesEnClair(texte: string): string {
  return texte.replace(/\d{4}-\d{2}-\d{2}/g, (d) => date(d));
}

/** Valeur d'une métrique selon son format déclaré dans metriques.json. */
export function metrique(v: number, format: string): string {
  if (format === "pourcentage") {
    const d = Math.abs(v) >= 10 ? 0 : 1;
    return new Intl.NumberFormat("fr-FR", { style: "percent", minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
  }
  if (format === "trimestres") {
    return `${nfx(1).format(v)} trimestre${Math.abs(v) >= 2 ? "s" : ""}`;
  }
  return nfx(Math.abs(v) >= 100 ? 0 : 1).format(v);
}
