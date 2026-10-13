/**
 * Synthèse en tête de fiche — étape 4d.
 *
 * L'outil doit se lire plus vite qu'une page de courtier : une ligne par axe, en mots
 * clairs, avec les chiffres qui la justifient. Les quarante métriques restent
 * disponibles, repliées plus bas.
 *
 * - Valorisation : un VERDICT, calculé à l'ingestion (metriques.verdict_valorisation) :
 *   le multiple de référence du jour face à sa propre histoire sur 5 ans. Ce composant
 *   ne décide de rien, il compose la phrase à partir du palier stocké.
 * - Qualité, croissance, dilution : un CHIFFRE CLÉ, sans verdict. Les juger demande la
 *   comparaison au secteur, en phase 3.
 *
 * Vocabulaire descriptif (mémoire, §8) : « moins chère que d'habitude », jamais
 * « peu chère ».
 */
import config from "@/lib/metriques.json";
import { date, metrique } from "@/lib/format";
import type { LigneMetrique, ResumeHistorique } from "@/lib/queries";
import { phrasePosition, type Groupes } from "@/lib/groupes";
import RegleHistorique, { regleAffichable } from "./RegleHistorique";

const VERDICTS: Record<string, string> = {
  parmi_plus_bas: "Parmi ses valorisations les plus basses des 5 dernières années",
  moins_cher: "Moins chère que d'habitude",
  moyenne: "Dans sa moyenne des 5 dernières années",
  plus_cher: "Plus chère que d'habitude",
  parmi_plus_hauts: "Parmi ses valorisations les plus élevées des 5 dernières années",
};

const LIBELLE_COURT: Record<string, string> = {
  per: "PER",
  prix_ventes: "prix sur ventes",
  prix_valeur_comptable: "prix sur valeur comptable",
  prix_ffo: "prix sur FFO",
};

type Ligne = {
  titre: string; phrase: string; detail?: string; comparaison?: string | null;
  perte?: boolean; regle?: React.ReactNode;
};

/** Première métrique disponible d'une liste, avec son identifiant. */
function premiere(par: Map<string, LigneMetrique>, ids: string[]) {
  for (const id of ids) {
    const l = par.get(id);
    if (l && l.value !== null) return { id, v: l.value };
  }
  return null;
}

const horizon = (id: string) => (id.endsWith("5a") ? "5 ans" : id.endsWith("3a") ? "3 ans" : "1 an");
const pct = (v: number) => metrique(Math.abs(v), "pourcentage");

export default function Synthese({
  lignes,
  historiques,
  profil,
  auJour,
  groupes,
}: {
  lignes: LigneMetrique[];
  historiques: ResumeHistorique[];
  profil: string;
  auJour: string | null;
  groupes: Groupes;
}) {
  const par = new Map(lignes.map((l) => [l.metric_id, l]));
  const resumes = new Map(historiques.map((h) => [h.metric_id, h]));
  const out: Ligne[] = [];

  // --- Valorisation : verdict face à l'historique ----------------------------------
  const verdict = par.get("_verdict_valorisation");
  const [code, mid] = (verdict?.raison ?? "").split("|");
  if (verdict?.value !== null && verdict?.value !== undefined && VERDICTS[code] && mid) {
    const v = par.get(mid)?.value ?? null;
    const r = resumes.get(mid);
    const def = config.metriques.find((m) => m.id === mid)!;
    const ref = (config.reference_valorisation as Record<string, string[]>)[profil]?.[0];
    // Repli : le multiple de référence du profil n'était pas applicable.
    const repli = ref && ref !== mid && par.get(ref)?.raison
      ? ` Mesuré sur le ${LIBELLE_COURT[mid]} : ${LIBELLE_COURT[ref] ?? ref} non applicable, ${par.get(ref)!.raison}.`
      : "";
    out.push({
      titre: "Valorisation",
      phrase: VERDICTS[code] + ".",
      detail: v !== null && r?.mediane != null
        ? `${LIBELLE_COURT[mid].replace(/^./, (c) => c.toUpperCase())} de ${metrique(v, def.format)}, `
          + `contre une médiane de ${metrique(r.mediane, def.format)} sur 5 ans`
          + (auJour ? `, au ${date(auJour)}.` : ".") + repli
        : undefined,
      // Seconde affirmation, distincte de la première : face aux sociétés comparables.
      comparaison: phrasePosition(par.get(mid), def.format, groupes),
      regle: v !== null && regleAffichable(r, v)
        ? <RegleHistorique actuelle={v} resume={r!} format={def.format} libelle={def.libelle} />
        : undefined,
    });
  } else {
    out.push({ titre: "Valorisation", phrase: "Pas assez d'historique pour situer sa valorisation." });
  }

  // --- Qualité : un chiffre clé ------------------------------------------------------
  const qualite = premiere(par, profil === "financial" ? ["roe"]
    : profil === "reit" ? ["marge_nette"] : ["marge_operationnelle", "marge_nette"]);
  if (qualite) {
    const lib = config.metriques.find((m) => m.id === qualite.id)!.libelle;
    out.push({
      titre: "Qualité",
      phrase: qualite.v < 0
        ? `${lib} négative, à ${metrique(qualite.v, "pourcentage")} : la société est en perte.`
        : `${lib} de ${metrique(qualite.v, "pourcentage")}.`,
      comparaison: phrasePosition(par.get(qualite.id), "pourcentage", groupes),
      perte: qualite.v < 0,
    });
  }

  // --- Croissance : le chiffre d'affaires --------------------------------------------
  const ca = premiere(par, ["cagr_ca_5a", "cagr_ca_3a"]);
  if (ca) {
    out.push({
      titre: "Croissance",
      phrase: `Chiffre d'affaires en ${ca.v >= 0 ? "hausse" : "baisse"} de ${pct(ca.v)} par an `
        + `sur ${horizon(ca.id)}.`,
      comparaison: phrasePosition(par.get(ca.id), "pourcentage", groupes),
    });
  }

  // --- Dilution : le nombre d'actions ------------------------------------------------
  const act = premiere(par, ["cagr_actions_5a", "cagr_actions_3a", "cagr_actions_1a"]);
  if (act) {
    const h = horizon(act.id);
    out.push({
      titre: "Dilution",
      phrase: Math.abs(act.v) < 0.005
        ? `Nombre d'actions stable sur ${h}, à ${metrique(act.v, "pourcentage")} par an.`
        : act.v > 0
          ? `Nombre d'actions en hausse de ${pct(act.v)} par an sur ${h} : chaque action détient une part plus petite de l'entreprise.`
          : `Nombre d'actions en baisse de ${pct(act.v)} par an sur ${h} : la société rachète ses actions.`,
      comparaison: phrasePosition(par.get(act.id), "pourcentage", groupes),
    });
  }

  return (
    <section aria-labelledby="titre-synthese" className="mt-10">
      <h2 id="titre-synthese" className="sr-only">En bref</h2>
      <dl className="border-t border-filet">
        {out.map((l) => (
          <div key={l.titre}
               className="grid grid-cols-1 gap-x-6 gap-y-1 border-b border-filet py-4 sm:grid-cols-[8.5rem_1fr]">
            <dt className="text-encre-seconde">{l.titre}</dt>
            <dd>
              <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
                <p className={`text-lg font-semibold leading-snug ${l.perte ? "text-perte" : ""}`}>{l.phrase}</p>
                {l.regle}
              </div>
              {l.detail && <p className="mt-1 max-w-[62ch] text-encre-seconde">{l.detail}</p>}
              {l.comparaison && <p className="mt-1 max-w-[62ch] text-encre-seconde">{l.comparaison}</p>}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
