/**
 * Fiche valeur.
 *
 * Direction visuelle : le registre comptable. Une seule famille, Public Sans ; papier,
 * encre bleu-noir, bande verte d'aide à la lecture des lignes, rouge réservé aux pertes.
 * Rien n'est coloré pour juger : un PER élevé n'est ni rouge ni vert.
 *
 * Garde-fous du mémoire tenus ici : toute donnée porte sa date, la qualité des données
 * est visible, chaque exercice renvoie à son dépôt EDGAR, une valeur redressée est
 * signalée comme telle, une valeur absente dit pourquoi.
 */
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import Recherche from "@/app/composants/Recherche";
import Graphe from "@/app/composants/Graphe";
import Metriques from "@/app/composants/Metriques";
import { cours, exercices, lienEdgar, metriques, series, societe } from "@/lib/queries";
import { actions, date, dollars, nombre } from "@/lib/format";

export const revalidate = 3600;

export default async function FicheValeur({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker } = await params;
  const demande = decodeURIComponent(ticker);
  const s = await societe(demande);
  if (!s) notFound();
  if (s.ticker !== demande) permanentRedirect(`/v/${encodeURIComponent(s.ticker)}`);

  // Lectures indépendantes, lancées en parallèle : quatre allers-retours enchaînés vers
  // le pooler additionneraient leurs latences.
  const [fy, points, lignes, pointsSeries] = await Promise.all([
    exercices(s.cik), cours(s.company_id), metriques(s.company_id), series(s.company_id),
  ]);
  const horsPerimetre = !["standard", "financial", "reit"].includes(s.mapping_profile);
  const qualite = s.data_quality_score === null ? null : Math.round(s.data_quality_score * 100);

  return (
    <main className="mx-auto max-w-3xl px-5 pb-16 pt-6 sm:px-8">
      <nav className="flex items-center gap-6">
        <Link href="/" className="text-sm text-encre-seconde underline underline-offset-4">Accueil</Link>
        <div className="flex-1"><Recherche /></div>
      </nav>

      <header className="mt-12">
        <h1 className="text-[2.75rem] font-semibold leading-none tracking-tight">{s.ticker}</h1>
        <p className="mt-3 text-xl">{s.name}</p>
        <p className="mt-5 max-w-[68ch] leading-relaxed text-encre-seconde">
          {s.market_cap !== null ? (
            <>Capitalisation de {dollars(s.market_cap)} au {date(s.market_cap_as_of)}
              {s.market_cap_uncertain && ", incertaine : plusieurs classes d'actions"}. </>
          ) : "Capitalisation inconnue. "}
          {qualite !== null && <>Données complètes à {qualite} % sur {s.fiscal_years_available ?? 0} exercices, </>}
          dernier dépôt le {date(s.last_xbrl_filing)}. {s.exchange}, CIK {s.cik}.
        </p>
      </header>

      {horsPerimetre && (
        <p className="mt-8 border-l-2 border-encre pl-4">
          Valeur hors du périmètre d'analyse ({s.mapping_profile}) : ses comptes ne sont pas
          exploités, seuls ses cours sont suivis.
        </p>
      )}

      <section className="mt-12" aria-labelledby="titre-cours">
        <h2 id="titre-cours" className="sr-only">Cours</h2>
        <Graphe points={points} profondeurVisee={s.history_years} />
      </section>

      {!horsPerimetre && (
        <div className="mt-14">
          <Metriques lignes={lignes} series={pointsSeries} />
        </div>
      )}

      <section className="mt-14" aria-labelledby="titre-exercices">
        <div className="border-b border-filet pb-2">
          <h2 id="titre-exercices" className="text-xl font-semibold">Exercices déposés</h2>
        </div>
        {fy.length === 0 ? (
          <p className="mt-4 text-encre-seconde">
            Aucun exercice annuel déposé : la société est cotée depuis moins d'un an et n'a
            publié que des rapports trimestriels.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-encre-seconde">
                  <th className="px-2 py-2 font-normal">Clôture</th>
                  <th className="px-2 py-2 text-right font-normal">Chiffre d'affaires</th>
                  <th className="px-2 py-2 text-right font-normal">Résultat net</th>
                  <th className="px-2 py-2 text-right font-normal">BPA dilué</th>
                  <th className="px-2 py-2 text-right font-normal">Actions diluées</th>
                  <th className="px-2 py-2 font-normal">Source</th>
                </tr>
              </thead>
              <tbody>
                {fy.map((e) => {
                  const lien = lienEdgar(s.cik, e.accn);
                  const redresse = (e.scale_corrected ?? []).length > 0;
                  return (
                    <tr key={e.period_end} className="odd:bg-bande">
                      <td className="whitespace-nowrap px-2 py-2">{date(e.period_end)}</td>
                      <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums">{dollars(e.revenue)}</td>
                      <td className={`whitespace-nowrap px-2 py-2 text-right tabular-nums ${(e.net_income ?? 0) < 0 ? "text-perte" : ""}`}>{dollars(e.net_income)}</td>
                      <td className={`whitespace-nowrap px-2 py-2 text-right tabular-nums ${(e.eps_diluted ?? 0) < 0 ? "text-perte" : ""}`}>{nombre(e.eps_diluted)}</td>
                      <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums">
                        {actions(e.shares_diluted)}
                        {redresse && <span title="Ordre de grandeur redressé : le dépôt d'origine déclarait ce nombre en milliers"> *</span>}
                      </td>
                      <td className="whitespace-nowrap px-2 py-2">
                        {lien ? (
                          <a href={lien} className="underline underline-offset-4" target="_blank" rel="noreferrer"
                             title="Document d'où provient la valeur. Un 10-K reprend les deux exercices précédents : ce n'est pas forcément la première publication.">
                            dépôt du {date(e.filed)}
                          </a>
                        ) : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {fy.some((e) => (e.scale_corrected ?? []).length > 0) && (
              <p className="mt-2 text-sm text-encre-seconde">
                * Nombre d'actions redressé : le dépôt d'origine le déclarait en milliers.
              </p>
            )}
          </div>
        )}
      </section>

      <footer className="mt-14 text-sm text-encre-seconde">
        {[
          s.prices_refreshed_at && `Cours mis à jour le ${date(s.prices_refreshed_at)}`,
          s.fundamentals_refreshed_at && `comptes le ${date(s.fundamentals_refreshed_at)}`,
        ].filter(Boolean).join(", ") || "Date de mise à jour inconnue"}.
        {!s.in_universe && " Valeur hors de l'univers suivi : capitalisation ou volume sous les seuils."}
      </footer>
    </main>
  );
}
