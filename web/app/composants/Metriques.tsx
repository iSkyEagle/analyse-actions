/**
 * Métriques par axe — lecture seule.
 *
 * Tout vient de la base, calculé par ingestion/metriques.py ; libellés, formats et ordre
 * viennent de lib/metriques.json, exporté du même fichier Python. Aucune formule ici :
 * c'est ce qui garantit que la fiche, la comparaison et le screener affichent le même
 * chiffre.
 *
 * Une valeur absente s'écrit avec sa raison, jamais comme un zéro ni un tiret muet
 * (règle 2 du mémoire).
 */
import config from "@/lib/metriques.json";
import { datesEnClair, metrique } from "@/lib/format";
import type { LigneMetrique, PointSerie } from "@/lib/queries";
import Tendance from "./Tendance";

type Def = (typeof config.metriques)[number];

export default function Metriques({
  lignes,
  series,
}: {
  lignes: LigneMetrique[];
  series: PointSerie[];
}) {
  if (lignes.length === 0) {
    return (
      <p className="text-encre-seconde">
        Métriques pas encore calculées pour cette valeur. Elles le sont chaque soir de bourse,
        après la mise à jour des cours.
      </p>
    );
  }

  const parId = new Map(lignes.map((l) => [l.metric_id, l]));
  const seriesPar = new Map<string, PointSerie[]>();
  for (const p of series) {
    const l = seriesPar.get(p.metric_id);
    if (l) l.push(p);
    else seriesPar.set(p.metric_id, [p]);
  }
  const base = parId.get("_base_12_mois")?.raison;

  return (
    <div className="space-y-10">
      {config.axes.map((axe) => {
        const defs = config.metriques.filter((m) => m.axe === axe.id && parId.has(m.id));
        // Un axe dont toutes les métriques sont sans objet pour ce profil — une banque et
        // ses marges — n'est pas affiché : une liste de « sans objet » n'apprend rien.
        const utiles = defs.filter((m) => !parId.get(m.id)!.raison?.startsWith("sans objet"));
        if (utiles.length === 0) return null;
        const avecTendance = utiles.some((m) => m.serie);
        return (
          <section key={axe.id} aria-labelledby={`axe-${axe.id}`}>
            <div className="flex items-baseline justify-between gap-4 border-b border-filet pb-2">
              <h2 id={`axe-${axe.id}`} className="text-xl font-semibold">{axe.libelle}</h2>
              {avecTendance && <span className="hidden text-sm text-encre-seconde sm:inline">derniers exercices</span>}
            </div>
            <dl>
              {utiles.map((m) => (
                <Ligne key={m.id} def={m} ligne={parId.get(m.id)!} serie={seriesPar.get(m.id) ?? []} />
              ))}
            </dl>
            {axe.id === "valorisation" && base && (
              <p className="mt-3 text-sm text-encre-seconde">
                Flux sur douze mois : {datesEnClair(base)}.
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}

function Ligne({ def, ligne, serie }: { def: Def; ligne: LigneMetrique; serie: PointSerie[] }) {
  const v = ligne.value;
  const perte = def.perte && v !== null && v < 0;
  return (
    <div className="grid grid-cols-[1fr_auto] items-baseline gap-x-6 px-2 py-2 odd:bg-bande sm:grid-cols-[1fr_auto_5rem]">
      <dt title={def.note || undefined}>{def.libelle}</dt>
      <dd className={`text-right tabular-nums ${perte ? "text-perte" : ""}`}>
        {v !== null ? metrique(v, def.format) : (
          <span className="text-sm text-encre-seconde">{ligne.raison ?? "non disponible"}</span>
        )}
      </dd>
      <dd className="hidden justify-self-end sm:block" aria-hidden={serie.length < 2}>
        {def.serie && <Tendance points={serie} format={def.format} libelle={def.libelle} />}
      </dd>
    </div>
  );
}
