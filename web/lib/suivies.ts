/**
 * Valeurs suivies (étape 6) — mémorisées sur l'appareil, sans compte.
 *
 * Le site n'a qu'un mot de passe partagé, pas d'utilisateurs : la liste ne peut vivre
 * que dans le navigateur. Elle est donc propre à chaque appareil — et sur iPhone,
 * l'application installée et Safari ont chacun la leur.
 */
const CLE = "suivies-v1";
export const MAX_SUIVIES = 50;
export const EVENEMENT = "suivies-modifiees";

export function lireSuivies(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(CLE) ?? "[]");
    return Array.isArray(v) ? v.filter((t) => typeof t === "string").slice(0, MAX_SUIVIES) : [];
  } catch {
    return [];
  }
}

function ecrire(liste: string[]) {
  try {
    localStorage.setItem(CLE, JSON.stringify(liste.slice(0, MAX_SUIVIES)));
  } catch { /* stockage refusé (navigation privée) : la liste reste vide */ }
  window.dispatchEvent(new Event(EVENEMENT));
}

/** Ajoute en tête, ou retire. Renvoie le nouvel état. */
export function basculer(ticker: string): boolean {
  const liste = lireSuivies();
  const suivie = liste.includes(ticker);
  ecrire(suivie ? liste.filter((t) => t !== ticker) : [ticker, ...liste]);
  return !suivie;
}
