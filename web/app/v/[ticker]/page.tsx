/**
 * Fiche valeur — étape 1.
 *
 * Objectif : valider la chaîne complète (base -> requête serveur -> rendu) sur de
 * vraies données. Les garde-fous du mémoire sont posés dès maintenant, parce qu'ils
 * sont plus difficiles à ajouter après coup qu'à respecter d'emblée :
 *   - toute donnée affichée porte sa date ;
 *   - la qualité des données est visible ;
 *   - chaque exercice renvoie à son dépôt EDGAR ;
 *   - une valeur inférée ou redressée est signalée comme telle.
 */
import Link from "next/link";
import Recherche from "@/app/composants/Recherche";
import { notFound, permanentRedirect } from "next/navigation";
import { exercices, lienEdgar, societe } from "@/lib/queries";
import { actions, date, dollars, nombre } from "@/lib/format";

// Données rafraîchies chaque nuit : une heure de cache côté serveur suffit, et évite
// d'interroger la base à chaque ouverture de la même fiche.
export const revalidate = 3600;

export default async function FicheValeur({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker } = await params;
  const demande = decodeURIComponent(ticker);
  const s = await societe(demande);
  if (!s) notFound();
  // Une seule URL par valeur : /v/brk.b et /v/BRKB renvoient vers /v/BRK-B.
  if (s.ticker !== demande) permanentRedirect(`/v/${encodeURIComponent(s.ticker)}`);

  const fy = await exercices(s.cik);
  const horsPerimetre = !["standard", "financial", "reit"].includes(s.mapping_profile);

  return (
    <main className="mx-auto max-w-3xl px-6 py-12">
      <div className="flex items-center gap-4">
        <Link href="/" className="text-sm underline">Accueil</Link>
        <div className="flex-1"><Recherche /></div>
      </div>

      <header className="mt-6">
        <h1 className="text-3xl font-semibold">{s.ticker}</h1>
        <p className="mt-1 text-lg">{s.name}</p>
        <p className="mt-1 text-sm opacity-70">
          {s.exchange}{s.sector ? `, ${s.sector}` : ""}, CIK {s.cik}
        </p>
      </header>

      {horsPerimetre && (
        <p className="mt-6 rounded border px-4 py-3 text-sm">
          Cette valeur est hors du périmètre d'analyse ({s.mapping_profile}) : ses comptes
          ne sont pas exploités. Seuls ses cours sont suivis.
        </p>
      )}

      <section className="mt-8 grid grid-cols-2 gap-6 sm:grid-cols-3">
        <div>
          <p className="text-sm opacity-70">Capitalisation</p>
          <p className="text-xl">{dollars(s.market_cap)}</p>
          <p className="text-xs opacity-70">
            au {date(s.market_cap_as_of)}
            {s.market_cap_uncertain && ", incertaine (plusieurs classes d'actions)"}
          </p>
        </div>
        <div>
          <p className="text-sm opacity-70">Qualité des données</p>
          <p className="text-xl">
            {s.data_quality_score === null ? "—" : `${Math.round(s.data_quality_score * 100)} %`}
          </p>
          <p className="text-xs opacity-70">{s.fiscal_years_available ?? 0} exercices disponibles</p>
        </div>
        <div>
          <p className="text-sm opacity-70">Dernier dépôt</p>
          <p className="text-xl">{date(s.last_xbrl_filing)}</p>
          <p className="text-xs opacity-70">comptes mis à jour le {date(s.fundamentals_refreshed_at)}</p>
        </div>
      </section>

      <section className="mt-10">
        <h2 className="text-lg font-semibold">Derniers exercices</h2>
        {fy.length === 0 ? (
          <p className="mt-3 text-sm">
            Aucun exercice annuel déposé. C'est le cas des sociétés introduites en bourse
            depuis moins d'un an, qui n'ont encore publié que des rapports trimestriels.
          </p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left">
                  <th className="py-2 pr-4 font-medium">Clôture</th>
                  <th className="py-2 pr-4 text-right font-medium">Chiffre d'affaires</th>
                  <th className="py-2 pr-4 text-right font-medium">Résultat net</th>
                  <th className="py-2 pr-4 text-right font-medium">BPA dilué</th>
                  <th className="py-2 pr-4 text-right font-medium">Actions diluées</th>
                  <th className="py-2 font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {fy.map((e) => {
                  const lien = lienEdgar(s.cik, e.accn);
                  const redresse = (e.scale_corrected ?? []).length > 0;
                  return (
                    <tr key={e.period_end} className="border-t">
                      <td className="py-2 pr-4">{date(e.period_end)}</td>
                      <td className="py-2 pr-4 text-right tabular-nums">{dollars(e.revenue)}</td>
                      <td className="py-2 pr-4 text-right tabular-nums">{dollars(e.net_income)}</td>
                      <td className="py-2 pr-4 text-right tabular-nums">{nombre(e.eps_diluted)}</td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {actions(e.shares_diluted)}
                        {redresse && (
                          <span title="Ordre de grandeur redressé : le dépôt d'origine déclarait ce nombre en milliers">
                            {" "}*
                          </span>
                        )}
                      </td>
                      <td className="py-2">
                        {lien ? (
                          <a href={lien} className="underline" target="_blank" rel="noreferrer">
                            déposé le {date(e.filed)}
                          </a>
                        ) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {fy.some((e) => (e.scale_corrected ?? []).length > 0) && (
              <p className="mt-2 text-xs opacity-70">
                * Nombre d'actions redressé : le dépôt d'origine le déclarait en milliers.
              </p>
            )}
          </div>
        )}
      </section>

      <p className="mt-10 text-xs opacity-70">
        Cours mis à jour le {date(s.prices_refreshed_at)}.
        {!s.in_universe && " Valeur hors de l'univers suivi : capitalisation ou volume sous les seuils."}
      </p>
    </main>
  );
}
