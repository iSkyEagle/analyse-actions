/**
 * Comparaison de deux valeurs, ou d'une valeur face au S&P 500 — étape 5 (phase 2).
 *
 * Décidé le 14 octobre 2026 :
 *   - en tête, les deux notes globales, les profils en mots et les notes d'axe en regard ;
 *   - un graphe des deux cours en base 100, le S&P 500 en option ;
 *   - le détail des métriques côte à côte, replié par axe, sans couleur qui désigne un
 *     gagnant — le rouge reste réservé aux pertes ;
 *   - face au S&P 500, qui n'a pas de comptes, seules les mesures de cours ;
 *   - deux industries différentes : permis, avec un avertissement, chaque note étant
 *     calculée dans son propre groupe.
 * Comme ailleurs, aucune formule ici : tout est lu en base.
 */
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import config from "@/lib/metriques.json";
import Recherche from "@/app/composants/Recherche";
import GrapheComparaison, { type SerieComparee } from "@/app/composants/GrapheComparaison";
import { Jauge } from "@/app/composants/Synthese";
import { cours, metriques, notes, societe, type LigneMetrique, type Note, type Societe } from "@/lib/queries";
import { dollars, metrique } from "@/lib/format";
import { lienComparaison, TICKER_INDICE, versAdresse, versBase } from "@/lib/comparaison";
import { profilEnMots, surDix, type Axe } from "@/lib/notes";

export const revalidate = 3600;

type Cote = { s: Societe; lignes: LigneMetrique[]; notes: Note[]; indice: boolean };

const AXES_NOTES: [Axe | "globale", string][] = [
  ["globale", "Note globale"], ["valorisation", "Valorisation"], ["qualite", "Qualité"],
  ["croissance", "Croissance"], ["dilution", "Dilution et risque"],
];

export default async function Comparaison({ params }: { params: Promise<{ a: string; b: string }> }) {
  const { a, b } = await params;
  const [sa, sb] = await Promise.all([societe(versBase(a)), societe(versBase(b))]);
  if (!sa || !sb) notFound();
  // L'indice se lit toujours à droite ; une même valeur des deux côtés renvoie au choix.
  if (sa.ticker === TICKER_INDICE) permanentRedirect(lienComparaison(sb.ticker, sa.ticker));
  if (sa.ticker === sb.ticker) permanentRedirect(lienComparaison(sa.ticker));
  if (versAdresse(sa.ticker) !== a || versAdresse(sb.ticker) !== b) {
    permanentRedirect(lienComparaison(sa.ticker, sb.ticker));
  }

  const bIndice = sb.ticker === TICKER_INDICE;
  const [la, lb, na, nb, ca, cb, ci] = await Promise.all([
    metriques(sa.company_id), metriques(sb.company_id), notes(sa.company_id), notes(sb.company_id),
    cours(sa.company_id), cours(sb.company_id),
    // Le S&P 500 en option sur le graphe, sauf s'il est déjà l'un des deux côtés.
    bIndice ? Promise.resolve(null) : societe(TICKER_INDICE).then((i) => (i ? cours(i.company_id) : null)),
  ]);
  const A: Cote = { s: sa, lignes: la, notes: na, indice: false };
  const B: Cote = { s: sb, lignes: lb, notes: nb, indice: bIndice };
  const serie = (s: Societe, c: [string, number, number][]): SerieComparee =>
    ({ ticker: s.ticker, libelle: s.ticker === TICKER_INDICE ? "S&P 500" : s.ticker, points: c.map(([t, v]) => [t, v]) });

  const industriesDifferentes = !bIndice && sa.industry && sb.industry && sa.industry !== sb.industry;

  return (
    <main className="mx-auto max-w-3xl px-5 pb-16 pt-6 sm:px-8">
      <nav className="flex items-center gap-6">
        <Link href="/" className="text-sm text-encre-seconde underline underline-offset-4">Accueil</Link>
        <div className="flex-1"><Recherche /></div>
      </nav>

      <header className="mt-12">
        <p className="text-encre-seconde">Comparaison</p>
        <h1 className="mt-1 text-[2.25rem] font-semibold leading-tight tracking-tight">
          {sa.ticker} <span className="font-normal text-encre-seconde">face à</span> {bIndice ? "S&P 500" : sb.ticker}
        </h1>
        <div className="mt-4 grid gap-x-8 gap-y-3 sm:grid-cols-2">
          <Identite c={A} />
          <Identite c={B} />
        </div>
        <div className="mt-5 max-w-sm">
          <Recherche action={lienComparaison(sa.ticker)} prefixe={`${lienComparaison(sa.ticker)}/`}
                     libelle={`Comparer ${sa.ticker} à une autre valeur`}
                     indication={`Comparer ${sa.ticker} à une autre valeur`} />
          <p className="mt-1 text-sm text-encre-seconde">
            Changer la seconde valeur{!bIndice && <>, ou <Link href={lienComparaison(sa.ticker, TICKER_INDICE)}
              className="underline underline-offset-4">comparer au S&amp;P 500</Link></>}.
          </p>
        </div>
      </header>

      {industriesDifferentes && (
        <p className="mt-8 border-l-2 border-encre pl-4 text-sm leading-relaxed">
          {sa.ticker} ({sa.industry!.toLowerCase()}) et {sb.ticker} ({sb.industry!.toLowerCase()}) ne
          sont pas de la même industrie : chaque note situe la société dans son propre groupe. Une
          marge de 30 % n&apos;a pas le même sens chez l&apos;une et chez l&apos;autre.
        </p>
      )}

      {bIndice ? (
        <p className="mt-10 border-y border-filet py-4 text-encre-seconde">
          Le S&amp;P 500 n&apos;a pas de comptes : seules les mesures de cours se comparent — évolution,
          rendement total, volatilité, pire baisse, bêta.
        </p>
      ) : (
        <Notes A={A} B={B} />
      )}

      <section className="mt-12" aria-labelledby="titre-graphe">
        <h2 id="titre-graphe" className="sr-only">Évolution des cours</h2>
        <GrapheComparaison a={serie(sa, ca)} b={serie(sb, cb)}
                           indice={ci ? { ticker: TICKER_INDICE, libelle: "S&P 500", points: ci.map(([t, v]) => [t, v]) } : null} />
      </section>

      <Detail A={A} B={B} />
    </main>
  );
}

function Identite({ c }: { c: Cote }) {
  const { s } = c;
  if (c.indice) {
    return (
      <div>
        <p className="font-semibold">S&amp;P 500</p>
        <p className="text-sm text-encre-seconde">Indice des 500 grandes valeurs américaines</p>
      </div>
    );
  }
  return (
    <div>
      <Link href={`/v/${encodeURIComponent(s.ticker)}`} className="font-semibold underline underline-offset-4">
        {s.ticker}
      </Link>{" "}
      <span>{s.name}</span>
      <p className="text-sm text-encre-seconde">
        {s.industry ?? "Non classé"} · {s.market_cap !== null ? dollars(s.market_cap) : "capitalisation inconnue"}
        {s.market_cap_uncertain && " (incertaine)"}
      </p>
    </div>
  );
}

function Notes({ A, B }: { A: Cote; B: Cote }) {
  const note = (c: Cote, axe: string) => c.notes.find((n) => n.axis === axe);
  return (
    <section className="mt-10" aria-labelledby="titre-notes">
      <h2 id="titre-notes" className="sr-only">Notes</h2>
      <table className="w-full">
        <thead>
          <tr className="border-b border-filet text-left text-sm text-encre-seconde">
            <th className="py-2 pr-4 font-normal"><span className="sr-only">Axe</span></th>
            <th className="w-[38%] px-2 py-2 font-normal">{A.s.ticker}</th>
            <th className="w-[38%] px-2 py-2 font-normal">{B.s.ticker}</th>
          </tr>
        </thead>
        <tbody>
          {AXES_NOTES.map(([axe, libelle]) => (
            <tr key={axe} className={axe === "globale" ? "border-b border-filet" : "odd:bg-bande"}>
              <th scope="row" className="py-2 pr-4 text-left font-normal text-encre-seconde">{libelle}</th>
              {[A, B].map((c) => {
                const n = note(c, axe);
                return (
                  <td key={c.s.ticker} className="px-2 py-2 align-top">
                    {n?.score != null ? (
                      axe === "globale" ? (
                        <span className="flex flex-wrap items-center gap-3">
                          <span className="text-3xl font-semibold tabular-nums">
                            {surDix(n.score)}<span className="text-base font-normal text-encre-seconde">/10</span>
                          </span>
                          <Jauge note={surDix(n.score)} />
                        </span>
                      ) : (
                        <span className="text-lg font-semibold tabular-nums">
                          {surDix(n.score)}<span className="text-sm font-normal text-encre-seconde">/10</span>
                        </span>
                      )
                    ) : (
                      <span className="text-sm text-encre-seconde">
                        {axe === "globale" ? "pas de note globale" : "non noté"}
                      </span>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
          <tr>
            <th scope="row" className="py-3 pr-4 text-left align-top font-normal text-encre-seconde">Profil</th>
            {[A, B].map((c) => (
              <td key={c.s.ticker} className="px-2 py-3 align-top leading-snug">
                {profilEnMots(c.notes) ?? <span className="text-sm text-encre-seconde">aucun axe noté</span>}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </section>
  );
}

type Def = (typeof config.metriques)[number];

function Detail({ A, B }: { A: Cote; B: Cote }) {
  const pa = new Map(A.lignes.map((l) => [l.metric_id, l]));
  const pb = new Map(B.lignes.map((l) => [l.metric_id, l]));
  const utile = (l?: LigneMetrique) => l && !l.raison?.startsWith("sans objet");
  const axes = config.axes.map((axe) => ({
    axe,
    defs: config.metriques.filter((m) => m.axe === axe.id
      // Face à l'indice, seules les mesures de cours qu'il possède ; sinon toute métrique
      // utile d'un côté au moins.
      && (B.indice ? pb.has(m.id) : utile(pa.get(m.id)) || utile(pb.get(m.id)))),
  })).filter((x) => x.defs.length > 0);

  return (
    <section className="mt-14" aria-labelledby="titre-detail">
      <div className="border-b border-filet pb-2">
        <h2 id="titre-detail" className="text-xl font-semibold">Métriques côte à côte</h2>
      </div>
      <div className="mt-2">
        {axes.map(({ axe, defs }, i) => (
          // Replié par axe comme sur la fiche ; face à l'indice, les quelques mesures de
          // cours sont ouvertes d'emblée.
          <details key={axe.id} className="group border-b border-filet" open={B.indice || i === 0}>
            <summary className="flex cursor-pointer list-none items-center gap-3 py-3 [&::-webkit-details-marker]:hidden">
              <span aria-hidden className="inline-block transition-transform group-open:rotate-90">›</span>
              <span className="font-semibold">{axe.libelle}</span>
              <span className="text-sm text-encre-seconde">{defs.length} métrique{defs.length > 1 ? "s" : ""}</span>
            </summary>
            <table className="mb-4 w-full text-sm">
              <thead>
                <tr className="text-left text-encre-seconde">
                  <th className="px-2 py-1 font-normal"><span className="sr-only">Métrique</span></th>
                  <th className="w-[26%] px-2 py-1 text-right font-normal">{A.s.ticker}</th>
                  <th className="w-[26%] px-2 py-1 text-right font-normal">{B.indice ? "S&P 500" : B.s.ticker}</th>
                </tr>
              </thead>
              <tbody>
                {defs.map((d) => (
                  <tr key={d.id} className="odd:bg-bande">
                    <th scope="row" className="px-2 py-1.5 text-left font-normal">{d.libelle}</th>
                    <Valeur def={d} l={pa.get(d.id)} />
                    <Valeur def={d} l={pb.get(d.id)} />
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        ))}
      </div>
      <p className="mt-3 text-sm text-encre-seconde">
        Chaque chiffre est calculé comme sur la fiche de la valeur, où chaque ligne s&apos;ouvre
        sur son explication et sa position dans son groupe.
      </p>
    </section>
  );
}

function Valeur({ def, l }: { def: Def; l?: LigneMetrique }) {
  if (!l || l.value === null) {
    // Une valeur absente dit pourquoi (mémoire, garde-fous), en petit.
    return (
      <td className="px-2 py-1.5 text-right text-xs text-encre-seconde">
        {l?.raison ?? "non calculée"}
      </td>
    );
  }
  const perte = def.perte && l.value < 0;
  return (
    <td className={`whitespace-nowrap px-2 py-1.5 text-right tabular-nums ${perte ? "text-perte" : ""}`}>
      {metrique(l.value, def.format)}
    </td>
  );
}
