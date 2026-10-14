"use client";

/**
 * Valeurs suivies, sur l'accueil (étape 6) : note globale et verdict de valorisation
 * du jour. La liste est celle de l'appareil ; les chiffres viennent du serveur.
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { dollars } from "@/lib/format";
import { surDix, VERDICT_COURT } from "@/lib/notes";
import type { Suivie } from "@/lib/queries";
import { EVENEMENT, lireSuivies } from "@/lib/suivies";

export default function ListeSuivies() {
  const [lignes, setLignes] = useState<Suivie[] | null>(null);
  const [erreur, setErreur] = useState(false);

  useEffect(() => {
    const charger = () => {
      const t = lireSuivies();
      if (t.length === 0) { setLignes([]); return; }
      fetch(`/api/suivies?t=${encodeURIComponent(t.join(","))}`)
        .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
        .then((d: Suivie[]) => { setLignes(d); setErreur(false); })
        .catch(() => setErreur(true));
    };
    charger();
    window.addEventListener(EVENEMENT, charger);
    return () => window.removeEventListener(EVENEMENT, charger);
  }, []);

  if (lignes === null && !erreur) return null;
  return (
    <section className="mt-14" aria-labelledby="titre-suivies">
      <h2 id="titre-suivies" className="border-b border-filet pb-2 text-xl font-semibold">Valeurs suivies</h2>
      {erreur ? (
        <p className="mt-3 text-sm text-encre-seconde">Liste momentanément indisponible.</p>
      ) : lignes!.length === 0 ? (
        <p className="mt-3 text-sm leading-relaxed text-encre-seconde">
          Aucune valeur suivie. Sur une fiche, « Suivre cette valeur » l&apos;ajoute ici, avec sa
          note et sa valorisation du jour.
        </p>
      ) : (
        <ul>
          {lignes!.map((l) => (
            <li key={l.ticker} className="odd:bg-bande">
              <Link href={`/v/${encodeURIComponent(l.ticker)}`}
                    className="grid grid-cols-[1fr_auto] items-baseline gap-x-4 gap-y-0.5 px-2 py-2.5">
                <span className="min-w-0 truncate">
                  <span className="font-semibold underline underline-offset-4">{l.ticker}</span>{" "}
                  <span className="text-encre-seconde">{l.name}</span>
                </span>
                <span className="text-right tabular-nums">
                  {l.globale !== null ? <>{surDix(l.globale)}<span className="text-encre-seconde">/10</span></>
                    : <span className="text-sm text-encre-seconde" title="Moins de trois axes notés">—</span>}
                </span>
                <span className="text-sm text-encre-seconde">
                  {l.verdict && VERDICT_COURT[l.verdict] ? `Valorisation ${VERDICT_COURT[l.verdict]}` : "Valorisation non située"}
                </span>
                <span className="text-right text-sm tabular-nums text-encre-seconde">{dollars(l.market_cap)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      {lignes && lignes.length > 0 && (
        <p className="mt-2 text-sm text-encre-seconde">Liste mémorisée sur cet appareil.</p>
      )}
    </section>
  );
}
