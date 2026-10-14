/**
 * Sociétés comparables — étape 3d.
 *
 * Les cinq sociétés les plus proches, calculées chaque soir par ingestion/comparables.py :
 * même industrie et même profil (le secteur si l'industrie compte moins de six sociétés),
 * puis la taille d'abord, la marge brute, la marge opérationnelle et la croissance du
 * chiffre d'affaires. Ce composant ne choisit rien : il lit et met en regard.
 *
 * La société elle-même ouvre le tableau, pour lire chaque colonne par rapport à elle.
 * Une phrase commune dit comment la liste est faite, plutôt qu'une raison par ligne
 * (décidé le 14 octobre 2026).
 */
import Link from "next/link";
import { dollars, metrique } from "@/lib/format";
import type { Comparable } from "@/lib/queries";
import { nomGroupe, type Groupes } from "@/lib/groupes";

type Ligne = Omit<Comparable, "rang" | "niveau">;

export default function Comparables({ lignes, moi, groupes }: {
  lignes: Comparable[]; moi: Ligne; groupes: Groupes;
}) {
  if (lignes.length === 0) return null;
  const industrie = (groupes.industrie ?? "").toLowerCase();
  const nIndustrie = lignes.filter((l) => l.niveau === "industrie").length;
  const secteur = nomGroupe("secteur", groupes);
  const critere = "choisies d'abord par la taille, ensuite par la marge brute, la marge opérationnelle "
    + "et la croissance du chiffre d'affaires.";
  // Trois cas : l'industrie suffit ; elle est trop petite et ses membres passent d'abord,
  // complétés par le secteur ; elle ne compte aucune autre société suivie.
  const phrase = nIndustrie === lignes.length
    ? `Même industrie (${industrie}), ${critere}`
    : nIndustrie > 0
      ? `${nIndustrie === 1 ? "La seule autre société" : `Les ${nIndustrie} autres sociétés`} de l'industrie (${industrie}), `
        + `${nIndustrie === 1 ? "complétée" : "complétées"} par les plus proches du ${secteur}, ${critere}`
      : `Aucune autre société suivie dans l'industrie (${industrie}) : les plus proches du ${secteur}, ${critere}`;
  return (
    <section aria-labelledby="titre-comparables">
      <div className="border-b border-filet pb-2">
        <h2 id="titre-comparables" className="text-xl font-semibold">Sociétés comparables</h2>
      </div>
      <p className="mt-3 max-w-[62ch] text-sm leading-relaxed text-encre-seconde">
        {phrase}
      </p>
      {/* Téléphone : une fiche par société plutôt qu'un tableau de cinq colonnes qui
          déborde. Les mêmes chiffres, dans le même ordre. */}
      <ul className="mt-2 sm:hidden">
        <Fiche l={moi} soi />
        {lignes.map((l) => <Fiche key={l.ticker} l={l} />)}
      </ul>
      <div className="mt-2 hidden sm:block">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-encre-seconde">
              <th className="px-2 py-2 font-normal">Société</th>
              <th className="px-2 py-2 text-right font-normal">Capitalisation</th>
              <th className="px-2 py-2 text-right font-normal">Note globale</th>
              <th className="px-2 py-2 text-right font-normal">Croissance du CA, 3 ans</th>
              <th className="px-2 py-2 text-right font-normal">Marge opérationnelle</th>
            </tr>
          </thead>
          <tbody>
            <Rangee l={moi} soi />
            {lignes.map((l) => <Rangee key={l.ticker} l={l} />)}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Fiche({ l, soi = false }: { l: Ligne; soi?: boolean }) {
  const perte = l.marge_operationnelle !== null && l.marge_operationnelle < 0;
  return (
    <li className={`px-2 py-3 text-sm ${soi ? "border-b border-filet" : "odd:bg-bande"}`}>
      <p className="flex items-baseline gap-2">
        {soi ? <span className="font-semibold">{l.ticker}</span> : (
          <Link href={`/v/${encodeURIComponent(l.ticker)}`} className="font-semibold underline underline-offset-4">
            {l.ticker}
          </Link>
        )}
        <span className="truncate text-encre-seconde">{soi ? "cette société" : l.name}</span>
      </p>
      <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 tabular-nums">
        <dt className="text-encre-seconde">Capitalisation</dt>
        <dd className="text-right">{dollars(l.market_cap)}{l.market_cap_uncertain && " *"}</dd>
        <dt className="text-encre-seconde">Note globale</dt>
        <dd className="text-right">
          {l.globale !== null ? `${Math.round(l.globale / 10)}/10` : <Absent quoi="pas de note globale : moins de trois axes notés" />}
        </dd>
        <dt className="text-encre-seconde">Croissance du CA, 3 ans</dt>
        <dd className="text-right">
          {l.cagr_ca_3a !== null ? metrique(l.cagr_ca_3a, "pourcentage") : <Absent quoi="moins de 3 exercices ou chiffre d'affaires manquant" />}
        </dd>
        <dt className="text-encre-seconde">Marge opérationnelle</dt>
        <dd className={`text-right ${perte ? "text-perte" : ""}`}>
          {l.marge_operationnelle !== null ? metrique(l.marge_operationnelle, "pourcentage")
            : <Absent quoi="non disponible : la raison figure sur sa fiche" />}
        </dd>
      </dl>
    </li>
  );
}

function Rangee({ l, soi = false }: { l: Ligne; soi?: boolean }) {
  const perte = l.marge_operationnelle !== null && l.marge_operationnelle < 0;
  return (
    <tr className={soi ? "border-b border-filet font-semibold" : "odd:bg-bande"}>
      <td className="px-2 py-2">
        {soi ? (
          <span>{l.ticker} <span className="font-normal text-encre-seconde">(cette société)</span></span>
        ) : (
          <Link href={`/v/${encodeURIComponent(l.ticker)}`} className="underline underline-offset-4">
            {l.ticker}
          </Link>
        )}
        {!soi && <span className="block max-w-[22ch] truncate text-encre-seconde sm:max-w-[30ch]">{l.name}</span>}
      </td>
      <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums">
        {dollars(l.market_cap)}
        {l.market_cap_uncertain && <span title="Capitalisation incertaine : plusieurs catégories d'actions"> *</span>}
      </td>
      <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums">
        {l.globale !== null ? <>{Math.round(l.globale / 10)}<span className="text-encre-seconde">/10</span></>
          : <Absent quoi="pas de note globale : moins de trois axes notés" />}
      </td>
      <td className="whitespace-nowrap px-2 py-2 text-right tabular-nums">
        {l.cagr_ca_3a !== null ? metrique(l.cagr_ca_3a, "pourcentage") : <Absent quoi="moins de 3 exercices ou chiffre d'affaires manquant" />}
      </td>
      <td className={`whitespace-nowrap px-2 py-2 text-right tabular-nums ${perte ? "text-perte" : ""}`}>
        {l.marge_operationnelle !== null ? metrique(l.marge_operationnelle, "pourcentage")
          : <Absent quoi="non disponible : la raison figure sur sa fiche" />}
      </td>
    </tr>
  );
}

/** Tableau compact : le tiret porte sa raison au survol, la fiche la donne en entier. */
function Absent({ quoi }: { quoi: string }) {
  return <span className="text-encre-seconde" title={quoi}>—<span className="sr-only"> ({quoi})</span></span>;
}
