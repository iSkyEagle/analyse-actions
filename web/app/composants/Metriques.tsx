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
import type { LigneMetrique, PointSerie, ResumeHistorique } from "@/lib/queries";
import Tendance from "./Tendance";
import RegleHistorique, { regleAffichable, type Resume } from "./RegleHistorique";

type Def = (typeof config.metriques)[number];

export default function Metriques({
  lignes,
  series,
  historiques = [],
}: {
  lignes: LigneMetrique[];
  series: PointSerie[];
  historiques?: ResumeHistorique[];
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
  const resumes = new Map<string, Resume>(historiques.map((h) => [h.metric_id, h]));

  return (
    <div className="space-y-10">
      {config.axes.map((axe) => {
        const defs = config.metriques.filter((m) => m.axe === axe.id && parId.has(m.id));
        // Un axe dont toutes les métriques sont sans objet pour ce profil — une banque et
        // ses marges — n'est pas affiché : une liste de « sans objet » n'apprend rien.
        const utiles = defs.filter((m) => !parId.get(m.id)!.raison?.startsWith("sans objet"));
        if (utiles.length === 0) return null;
        const avecTendance = utiles.some((m) => m.serie);
        const avecRegle = utiles.some((m) => regleAffichable(resumes.get(m.id), parId.get(m.id)!.value));
        return (
          <section key={axe.id} aria-labelledby={`axe-${axe.id}`}>
            <div className="flex items-baseline justify-between gap-4 border-b border-filet pb-2">
              <h2 id={`axe-${axe.id}`} className="text-xl font-semibold">{axe.libelle}</h2>
              {avecTendance && <span className="hidden text-sm text-encre-seconde sm:inline">derniers exercices</span>}
              {avecRegle && <span className="hidden text-sm text-encre-seconde sm:inline">sur 5 ans</span>}
            </div>
            <dl>
              {utiles.map((m) => (
                <Ligne key={m.id} def={m} ligne={parId.get(m.id)!} serie={seriesPar.get(m.id) ?? []}
                       resume={resumes.get(m.id)} />
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

function Ligne({ def, ligne, serie, resume }: {
  def: Def; ligne: LigneMetrique; serie: PointSerie[]; resume?: Resume;
}) {
  const v = ligne.value;
  const perte = def.perte && v !== null && v < 0;
  // Une société qui n'a jamais versé de dividende : « 0,0 % » et une règle plate
  // n'apprendraient rien. On l'écrit.
  const sansDividende = def.id === "rendement_dividende" && v === 0;
  const regle = !sansDividende && regleAffichable(resume, v);
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-x-6 gap-y-1 px-2 py-2 odd:bg-bande sm:grid-cols-[1fr_auto_10rem]">
      <dt title={def.note || undefined}>{def.libelle}</dt>
      <dd className={`text-right tabular-nums ${perte ? "text-perte" : ""}`}>
        {sansDividende ? <span className="text-sm text-encre-seconde">aucun dividende</span>
          : v !== null ? metrique(v, def.format) : (
          <span className="text-sm text-encre-seconde">{ligne.raison ?? "non disponible"}</span>
        )}
      </dd>
      {regle ? (
        // La règle historique reste visible sur téléphone, sur sa propre ligne :
        // c'est l'élément fort de la fiche, pas un ornement qu'on retire.
        <dd className="col-span-2 sm:col-span-1 sm:justify-self-end">
          <RegleHistorique actuelle={v!} resume={resume!} format={def.format} libelle={def.libelle} />
        </dd>
      ) : (
        <dd className="hidden justify-self-end sm:block" aria-hidden={serie.length < 2}>
          {def.serie && <Tendance points={serie} format={def.format} libelle={def.libelle} />}
        </dd>
      )}
    </div>
  );
}
