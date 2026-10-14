/**
 * Adresses de la comparaison (étape 5) : /comparer/MSFT/ORCL, /comparer/MSFT/SP500.
 *
 * Le S&P 500 est une ligne de `companies` au ticker « ^GSPC » : illisible et mal venu
 * dans une adresse (le « ^ » s'encode en %5E). L'adresse dit « SP500 ».
 */
export const ALIAS_INDICE = "SP500";
export const TICKER_INDICE = "^GSPC";

export const versBase = (t: string) =>
  decodeURIComponent(t).toUpperCase() === ALIAS_INDICE ? TICKER_INDICE : decodeURIComponent(t);
export const versAdresse = (ticker: string) =>
  ticker === TICKER_INDICE ? ALIAS_INDICE : encodeURIComponent(ticker);
export const lienComparaison = (a: string, b?: string) =>
  `/comparer/${versAdresse(a)}${b ? `/${versAdresse(b)}` : ""}`;
