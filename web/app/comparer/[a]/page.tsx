/**
 * Choix de la seconde valeur d'une comparaison — étape 5.
 *
 * Trois entrées : les cinq sociétés comparables (étape 3d), le S&P 500, et la
 * recherche. Sans JavaScript, le formulaire de recherche arrive ici avec `?t=ORCL` et
 * l'aiguillage renvoie vers /comparer/MSFT/ORCL.
 */
import Link from "next/link";
import { notFound, permanentRedirect, redirect } from "next/navigation";
import Recherche from "@/app/composants/Recherche";
import { comparables, societe } from "@/lib/queries";
import { dollars } from "@/lib/format";
import { lienComparaison, TICKER_INDICE, versAdresse, versBase } from "@/lib/comparaison";
import { surDix } from "@/lib/notes";

export const revalidate = 3600;

export default async function ChoixComparaison({ params, searchParams }: {
  params: Promise<{ a: string }>; searchParams: Promise<{ t?: string }>;
}) {
  const [{ a }, { t }] = await Promise.all([params, searchParams]);
  const s = await societe(versBase(a));
  if (!s) notFound();
  if (s.ticker === TICKER_INDICE) redirect("/");
  if (t && t.trim()) {
    const autre = await societe(versBase(t.trim()));
    if (autre) redirect(lienComparaison(s.ticker, autre.ticker));
  }
  if (versAdresse(s.ticker) !== a) permanentRedirect(lienComparaison(s.ticker));
  const proches = await comparables(s.company_id);

  return (
    <main className="mx-auto max-w-3xl px-5 pb-16 pt-2 sm:px-8">
      <header className="mt-12">
        <p className="text-encre-seconde">Comparaison</p>
        <h1 className="mt-1 text-[2.25rem] font-semibold leading-tight tracking-tight">
          Comparer {s.ticker} à…
        </h1>
        <p className="mt-2">
          <Link href={`/v/${encodeURIComponent(s.ticker)}`} className="underline underline-offset-4">{s.name}</Link>
        </p>
      </header>

      <div className="mt-8 max-w-md">
        <Recherche autoFocus action={lienComparaison(s.ticker)} prefixe={`${lienComparaison(s.ticker)}/`}
                   libelle={`Comparer ${s.ticker} à une autre valeur`}
                   indication="Ticker ou nom de la seconde valeur" />
        {t && <p className="mt-2 border-l-2 border-encre pl-3 text-sm">Aucune valeur trouvée pour « {t} ».</p>}
      </div>

      <section className="mt-10" aria-labelledby="titre-suggestions">
        <h2 id="titre-suggestions" className="border-b border-filet pb-2 text-xl font-semibold">Suggestions</h2>
        <ul>
          <li className="border-b border-filet">
            <Link href={lienComparaison(s.ticker, TICKER_INDICE)} className="flex items-baseline gap-3 px-2 py-3 hover:bg-bande">
              <span className="font-semibold underline underline-offset-4">S&amp;P 500</span>
              <span className="text-encre-seconde">l&apos;indice de référence, sur les mesures de cours</span>
            </Link>
          </li>
          {proches.map((p) => (
            <li key={p.ticker} className="border-b border-filet">
              <Link href={lienComparaison(s.ticker, p.ticker)}
                    className="grid grid-cols-[1fr_auto] items-baseline gap-x-4 px-2 py-3 hover:bg-bande">
                <span>
                  <span className="font-semibold underline underline-offset-4">{p.ticker}</span>{" "}
                  <span className="text-encre-seconde">{p.name}</span>
                </span>
                <span className="text-right text-sm tabular-nums text-encre-seconde">
                  {dollars(p.market_cap)}{p.globale !== null && <> · {surDix(p.globale)}/10</>}
                </span>
              </Link>
            </li>
          ))}
        </ul>
        {proches.length > 0 && (
          <p className="mt-2 text-sm text-encre-seconde">
            Les sociétés comparables de {s.ticker}, choisies par la taille et le profil d&apos;activité.
          </p>
        )}
      </section>
    </main>
  );
}
