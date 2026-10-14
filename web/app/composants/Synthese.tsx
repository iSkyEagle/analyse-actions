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
 * - Qualité, croissance, dilution : un CHIFFRE CLÉ, sans verdict.
 * - Notes (étape 3c) : une note par axe et une note globale, sur 10, calculées à
 *   l'ingestion (metriques.noter) contre les sociétés comparables. Le profil en mots
 *   vient des mêmes notes et des seuils exportés dans metriques.json : rien n'est jugé ici.
 *
 * Vocabulaire descriptif (mémoire, §8) : « moins chère que d'habitude », jamais
 * « peu chère ».
 */
import config from "@/lib/metriques.json";
import { date, metrique } from "@/lib/format";
import type { LigneMetrique, Note, ResumeHistorique } from "@/lib/queries";
import { nomGroupe, phrasePosition, type Groupes } from "@/lib/groupes";
import { majuscule, motProfil, ORDRE_PROFIL, surDix, type Axe } from "@/lib/notes";
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
  axe: Axe; titre: string; phrase: string; detail?: string; comparaison?: string | null;
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
  notes = [],
}: {
  lignes: LigneMetrique[];
  historiques: ResumeHistorique[];
  profil: string;
  auJour: string | null;
  groupes: Groupes;
  notes?: Note[];
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
      axe: "valorisation", titre: "Valorisation",
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
    out.push({ axe: "valorisation", titre: "Valorisation", phrase: "Pas assez d'historique pour situer sa valorisation." });
  }

  // --- Qualité : un chiffre clé ------------------------------------------------------
  const qualite = premiere(par, profil === "financial" ? ["roe"]
    : profil === "reit" ? ["marge_nette"] : ["marge_operationnelle", "marge_nette"]);
  if (qualite) {
    const lib = config.metriques.find((m) => m.id === qualite.id)!.libelle;
    out.push({
      axe: "qualite", titre: "Qualité",
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
      axe: "croissance", titre: "Croissance",
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
      // Le risque de cours entre dans la note de cet axe, pour une faible part.
      axe: "dilution", titre: "Dilution et risque",
      phrase: Math.abs(act.v) < 0.005
        ? `Nombre d'actions stable sur ${h}, à ${metrique(act.v, "pourcentage")} par an.`
        : act.v > 0
          ? `Nombre d'actions en hausse de ${pct(act.v)} par an sur ${h} : chaque action détient une part plus petite de l'entreprise.`
          : `Nombre d'actions en baisse de ${pct(act.v)} par an sur ${h} : la société rachète ses actions.`,
      comparaison: phrasePosition(par.get(act.id), "pourcentage", groupes),
    });
  }

  // --- Notes --------------------------------------------------------------------------
  const parAxe = new Map(notes.map((n) => [n.axis, n]));
  // Un axe noté sans chiffre clé à montrer (cas rare) garde sa ligne : la note ne doit
  // pas apparaître dans le profil sans être visible à côté de son axe.
  for (const [axe, titre] of [["qualite", "Qualité"], ["croissance", "Croissance"],
                              ["dilution", "Dilution et risque"]] as [Axe, string][]) {
    const n = parAxe.get(axe);
    if (n?.score != null && !out.some((l) => l.axe === axe)) {
      out.push({ axe, titre, phrase: majuscule(motProfil(axe, n.score)) + "." });
    }
  }

  return (
    <section aria-labelledby="titre-synthese" className="mt-10">
      <h2 id="titre-synthese" className="sr-only">En bref</h2>
      {notes.length > 0 && <NoteGlobale notes={notes} />}
      <dl className="border-t border-filet">
        {out.map((l) => (
          <div key={l.axe}
               className="grid grid-cols-1 gap-x-6 gap-y-1 border-b border-filet py-4 sm:grid-cols-[8.5rem_1fr]">
            <dt className="flex items-baseline justify-between gap-4 text-encre-seconde sm:block">
              <span>{l.titre}</span>
              <NoteAxe note={parAxe.get(l.axe)}
                       aucun={aucunApplicable({ axe: l.axe, par, profil, groupes, verdict: code })} />
            </dt>
            <dd>
              <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
                <p className={`text-lg font-semibold leading-snug ${l.perte ? "text-perte" : ""}`}>{l.phrase}</p>
                {l.regle}
              </div>
              {l.detail && <p className="mt-1 max-w-[62ch] text-encre-seconde">{l.detail}</p>}
              {l.comparaison && <p className="mt-1 max-w-[62ch] text-encre-seconde">{l.comparaison}</p>}
              {parAxe.has(l.axe) && (
                <Criteres axe={l.axe} par={par} profil={profil} groupes={groupes} verdict={code} />
              )}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// --- Notes : composants -------------------------------------------------------------

/** Dix cases à l'encre : une position sur une échelle, sans couleur qui juge. */
export function Jauge({ note }: { note: number }) {
  return (
    <span aria-hidden className="inline-flex gap-[3px]">
      {Array.from({ length: 10 }, (_, i) => (
        <span key={i} className={`h-3 w-2 ${i < note ? "bg-encre" : "bg-filet"}`} />
      ))}
    </span>
  );
}

function NoteGlobale({ notes }: { notes: Note[] }) {
  const globale = notes.find((n) => n.axis === "globale");
  const notes_axes = ORDRE_PROFIL.map((a) => notes.find((n) => n.axis === a))
    .filter((n): n is Note => n?.score != null);
  const profil = notes_axes.map((n) => motProfil(n.axis as Axe, n.score!));
  return (
    <div className="mb-6">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
        <h3 className="text-encre-seconde">Note globale</h3>
        {globale?.score != null ? (
          <>
            <p className="text-[2.5rem] font-semibold leading-none tabular-nums">
              {surDix(globale.score)}<span className="text-xl font-normal text-encre-seconde">/10</span>
              <span className="sr-only"> : note de {surDix(globale.score)} sur 10</span>
            </p>
            <Jauge note={surDix(globale.score)} />
          </>
        ) : (
          <p className="text-lg">
            Pas de note globale : {notes_axes.length === 0 ? "aucun axe n'est noté"
              : notes_axes.length === 1 ? "un seul axe est noté" : `${notes_axes.length} axes sont notés`},
            il en faut trois.
          </p>
        )}
      </div>
      {profil.length > 0 && (
        <p className="mt-3 max-w-[62ch] text-lg leading-snug">{majuscule(profil.join(", "))}.</p>
      )}
      <details className="mt-2 max-w-[62ch] text-sm text-encre-seconde">
        <summary className="cursor-pointer underline decoration-dotted underline-offset-4">
          Comment la note est calculée
        </summary>
        <div className="mt-2 space-y-2 leading-relaxed">
          <p>
            Chaque critère est comparé aux sociétés de la même industrie, ou du secteur quand
            l'industrie en compte moins de quinze : 10 si la société fait mieux que toutes,
            5 si elle est au milieu. La valorisation se compare aussi à sa propre histoire
            sur cinq ans. Une valorisation bien notée est une valorisation modeste.
          </p>
          <p>
            Trois seuils plafonnent le critère concerné à 2, quel que soit le groupe : une
            dette nette de plus de quatre ans d'EBITDA, des intérêts couverts moins de deux
            fois, des actions en circulation qui augmentent de plus de 10 % par an.
          </p>
          <p>
            Un critère qui n'a pas de sens pour la société — un PER quand elle est en perte,
            une couverture des intérêts quand elle n'a pas de dette — sort du calcul. Une
            donnée manquante, elle, compte : un axe n'est noté que si la moitié au moins de
            ses critères sont disponibles. La note globale est la moyenne des axes notés, à
            partir de trois. Elle situe la
            société parmi ses comparables : ce n'est pas une recommandation.
          </p>
        </div>
      </details>
    </div>
  );
}

function NoteAxe({ note, aucun = false }: { note?: Note; aucun?: boolean }) {
  if (!note) return null;
  if (note.score === null) {
    // « Non noté » laisserait croire à une donnée manquante chez une biotech sans chiffre
    // d'affaires : aucun multiple n'a de sens pour elle, ce n'est pas un manque.
    return aucun ? (
      <span className="block text-right text-sm sm:text-left">aucun critère applicable</span>
    ) : (
      <span className="block text-right text-sm sm:text-left" title={`Critères disponibles : ${note.coverage_pct} %, il en faut la moitié.`}>
        non noté
      </span>
    );
  }
  return (
    <span className="block text-right sm:text-left">
      <span className="text-2xl font-semibold tabular-nums text-encre">{surDix(note.score)}</span>
      <span className="text-encre-seconde">/10</span>
      {note.coverage_pct < 100 && (
        <span className="block text-xs">sur {note.coverage_pct} % des critères</span>
      )}
    </span>
  );
}

type Critere = { id: string; poids: number; sens: "haut" | "bas" };
const NOTATION = config.notation as Record<Axe, Critere[]>;
const SANS_OBJET = new Set<string>(config.sans_objet_note);

/**
 * Les critères d'une note, dépliables sous son axe (règle 3) : valeur et position dans
 * le groupe de comparaison. Rien n'est recalculé : la note vient de la base, la position
 * aussi ; on montre de quoi elle est faite.
 */
type ArgsCriteres = { axe: Axe; par: Map<string, LigneMetrique>; profil: string; groupes: Groupes; verdict: string };

/** Vrai si aucun critère de l'axe n'a de sens pour la société (biotech sans chiffre d'affaires). */
function aucunApplicable(args: ArgsCriteres): boolean {
  const autres = lignesCriteres(args).filter((r) => r.cle !== "_historique");
  return autres.every((r) => r.raison?.startsWith("hors calcul"));
}

function Criteres(args: ArgsCriteres) {
  const rangs = lignesCriteres(args);
  if (rangs.length === 0) return null;
  return <ListeCriteres rangs={rangs} />;
}

function lignesCriteres({ axe, par, profil, groupes, verdict }: ArgsCriteres) {
  // Même repli que metriques.reference_notee : le premier multiple de référence qui a un
  // sens pour la société. Le multiple écarté reste affiché, hors calcul.
  const liste = (config.reference_valorisation as Record<string, string[]>)[profil] ?? ["per"];
  const interpretable = (m: string) => {
    const r = par.get(m)?.raison ?? "";
    return !r.startsWith("sans objet") && !SANS_OBJET.has(r);
  };
  const ref = liste.find(interpretable) ?? liste[0];
  const ecarte = ref !== liste[0] ? liste[0] : null;
  const rangs = NOTATION[axe].flatMap((c) => {
    if (c.id !== "_reference" && c.id === ref) return [];       // déjà compté comme référence
    if (c.id === "_historique") {
      return [{ cle: c.id, libelle: "Multiple de référence face à son historique sur 5 ans", poids: c.poids,
                sens: c.sens, valeur: null as string | null,
                position: VERDICTS[verdict]?.replace(/ des 5 dernières années$/, "").toLowerCase() ?? null,
                raison: VERDICTS[verdict] ? null : "pas assez d'historique" }];
    }
    const id = c.id === "_reference" ? ref : c.id;
    const l = par.get(id);
    const def = config.metriques.find((m) => m.id === id);
    // Sans objet pour ce profil : hors du calcul, donc hors de la liste.
    if (!def || !l || l.raison?.startsWith("sans objet")) return [];
    const avant = c.id === "_reference" && ecarte ? (() => {
      const de = config.metriques.find((m) => m.id === ecarte)!;
      return [{ cle: ecarte, libelle: de.libelle, poids: 1, sens: c.sens, valeur: null as string | null,
                position: null as string | null,
                raison: `hors calcul : ${par.get(ecarte)?.raison} ; son poids passe au ${def.libelle.toLowerCase()}` }];
    })() : [];
    const p = l.percentile;
    return [...avant, {
      cle: id, libelle: def.libelle, poids: c.poids, sens: c.sens,
      valeur: l.value !== null ? metrique(l.value, def.format) : null,
      position: p === null || !l.percentile_niveau ? null
        : `${p >= 50 ? `au-dessus de ${Math.min(p, 99)} %` : `en dessous de ${Math.min(100 - p, 99)} %`}`
          + ` · ${nomGroupe(l.percentile_niveau, groupes)}`,
      // Règle 2 : un critère non interprétable (PER en perte…) est hors calcul, pas
      // manquant. On le montre, pour qu'on voie pourquoi il ne pèse pas.
      raison: l.value === null
        ? (SANS_OBJET.has(l.raison ?? "") ? `hors calcul : ${l.raison}` : (l.raison ?? "non disponible"))
        : p === null ? "groupe de comparaison trop petit" : null,
    }];
  });
  return rangs;
}

function ListeCriteres({ rangs }: { rangs: ReturnType<typeof lignesCriteres> }) {
  return (
    <details className="group/crit mt-2 text-sm">
      <summary className="cursor-pointer list-none text-encre-seconde underline decoration-dotted underline-offset-4
                          [&::-webkit-details-marker]:hidden">
        <span className="group-open/crit:hidden">Voir les {rangs.length} critères de la note</span>
        <span className="hidden group-open/crit:inline">Masquer les critères</span>
      </summary>
      <p className="mt-2 text-encre-seconde">
        Position parmi les sociétés comparables. ↑ : plus la valeur est haute, meilleure est
        la note ; ↓ : plus elle est basse.
      </p>
      {/* Liste plutôt que tableau : sur téléphone, le libellé prend sa ligne et la valeur
          passe dessous, au lieu de trois colonnes écrasées. */}
      <ul className="mt-1">
        {rangs.map((r) => (
          <li key={r.cle}
              className="grid grid-cols-[1fr_auto] gap-x-4 px-2 py-1.5 odd:bg-bande sm:grid-cols-[1fr_5rem_14rem]">
            <span>
              <span aria-label={r.sens === "haut" ? "plus haut est mieux noté" : "plus bas est mieux noté"}>
                {r.sens === "haut" ? "↑" : "↓"}
              </span>{" "}
              {r.libelle}
              {r.poids !== 1 && <span className="text-encre-seconde"> (poids {String(r.poids).replace(".", ",")})</span>}
            </span>
            <span className="text-right tabular-nums">{r.valeur ?? ""}</span>
            <span className="col-span-2 text-encre-seconde sm:col-span-1">{r.raison ?? r.position}</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
