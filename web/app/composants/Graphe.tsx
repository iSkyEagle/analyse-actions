"use client";

/**
 * Graphe de cours — clôture ajustée et volume.
 *
 * Rendu dans un <canvas> par lightweight-charts (TradingView) : conçu pour les séries
 * financières, il tient les ~2 500 séances de dix ans sans effort. Le logo
 * d'attribution reste affiché : la licence de la bibliothèque exige un lien vers
 * TradingView, et ce logo suffit à la satisfaire.
 *
 * Un canvas est opaque pour un lecteur d'écran : le conteneur porte un résumé textuel,
 * et la légende sous le graphe donne la période et la dernière clôture en clair.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AreaSeries, ColorType, createChart, HistogramSeries,
  type IChartApi, type UTCTimestamp,
} from "lightweight-charts";
import type { PointCours } from "@/lib/queries";

type Plage = "1a" | "5a" | "10a" | "tout";
const PLAGES: { id: Plage; libelle: string; ans: number | null }[] = [
  { id: "1a", libelle: "1 an", ans: 1 },
  { id: "5a", libelle: "5 ans", ans: 5 },
  { id: "10a", libelle: "10 ans", ans: 10 },
  { id: "tout", libelle: "Tout", ans: null },
];

// 'AAAA-MM-JJ' -> secondes UTC. Construit en UTC explicite : un `new Date('2024-03-15')`
// est bien en UTC, mais `new Date(2024, 2, 15)` serait en heure locale — la même
// famille de décalage d'un jour que celle corrigée côté serveur à l'étape 1.
const versTemps = (d: string) => {
  const [a, m, j] = d.split("-").map(Number);
  return (Date.UTC(a, m - 1, j) / 1000) as UTCTimestamp;
};

// « 0,7 an », « 1,5 an », « 2 ans » : en français le pluriel commence à 2.
const duree = (n: number) =>
  `${n.toLocaleString("fr-FR", { maximumFractionDigits: 1 })} ${n < 2 ? "an" : "ans"}`;

// Une plage est proposée dès que 90 % de sa durée est couverte. Une seule règle, pour
// les boutons comme pour la plage par défaut : avec deux seuils distincts, une valeur
// à 4,997 ans d'historique avait le bouton « 5 ans » actif mais s'ouvrait sur « Tout ».
const TOLERANCE = 0.9;

const fmtDate = (d: string) => {
  const [a, m, j] = d.split("-").map(Number);
  return new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(a, m - 1, j)));
};

export default function Graphe({
  points,
  profondeurVisee,
}: {
  points: PointCours[];
  profondeurVisee: number;
}) {
  const conteneur = useRef<HTMLDivElement>(null);
  const graphe = useRef<IChartApi | null>(null);

  const premier = points[0]?.[0];
  const dernier = points[points.length - 1];
  const anneesDispo = premier && dernier
    ? (versTemps(dernier[0]) - versTemps(premier)) / (365.25 * 86400)
    : 0;

  // Plage par défaut : 5 ans, horizon de la médiane des multiples. Si l'historique est
  // plus court, on affiche tout plutôt qu'une fenêtre à moitié vide.
  const disponible = (ans: number | null) => ans === null || anneesDispo >= ans * TOLERANCE;
  const [plage, setPlage] = useState<Plage>(disponible(5) ? "5a" : "tout");

  // Décimales selon le niveau de prix : 2 au-dessus d'un dollar, 4 en dessous.
  const precision = dernier && dernier[1] < 1 ? 4 : 2;

  const donnees = useMemo(() => ({
    prix: points.map(([t, c]) => ({ time: versTemps(t), value: c })),
    volumes: points.map(([t, , v]) => ({ time: versTemps(t), value: v })),
  }), [points]);

  useEffect(() => {
    const el = conteneur.current;
    if (!el || points.length === 0) return;

    // Le canvas ne lit pas les variables CSS : on relève les couleurs effectives du
    // thème à la création, pour suivre le mode clair ou sombre du système.
    const style = getComputedStyle(el);
    const texte = style.color;
    const fond = getComputedStyle(document.body).backgroundColor || "transparent";

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: fond },
        textColor: texte,
        attributionLogo: true,
      },
      localization: {
        locale: "fr-FR",
        priceFormatter: (p: number) =>
          new Intl.NumberFormat("fr-FR", { minimumFractionDigits: precision, maximumFractionDigits: precision }).format(p),
      },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(128,128,128,0.15)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false },
    });

    const prix = chart.addSeries(AreaSeries, {
      lineColor: texte,
      lineWidth: 2,
      topColor: "rgba(128,128,128,0.25)",
      bottomColor: "rgba(128,128,128,0.02)",
      priceLineVisible: false,
      priceFormat: { type: "price", precision, minMove: 1 / 10 ** precision },
    });
    prix.setData(donnees.prix);
    // Marge basse réservée à la bande de volume : sans elle, la courbe descendait dans
    // l'histogramme dès que le cours touchait le bas de sa fourchette — visible à la
    // première capture d'écran, invisible dans le HTML.
    prix.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.2 } });

    // Volume en surimpression, dans les 14 % inférieurs, sur une échelle à part. Teinte
    // légère : c'est un contexte, pas l'information principale.
    const vol = chart.addSeries(HistogramSeries, {
      priceScaleId: "",
      color: "rgba(128,128,128,0.22)",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.86, bottom: 0 } });
    vol.setData(donnees.volumes);

    graphe.current = chart;
    return () => {
      chart.remove();
      graphe.current = null;
    };
  }, [donnees, precision, points.length]);

  // Application de la plage choisie, sans reconstruire le graphe.
  useEffect(() => {
    const chart = graphe.current;
    if (!chart || !dernier) return;
    const p = PLAGES.find((x) => x.id === plage);
    if (!p?.ans) {
      chart.timeScale().fitContent();
      return;
    }
    const fin = versTemps(dernier[0]);
    chart.timeScale().setVisibleRange({
      from: Math.max(versTemps(premier!), fin - p.ans * 365.25 * 86400) as UTCTimestamp,
      to: fin,
    });
  }, [plage, premier, dernier]);

  if (points.length === 0) {
    return (
      <p className="text-sm">
        Aucun cours enregistré pour cette valeur. Les cours sont récupérés chaque soir
        de bourse ; une valeur ajoutée récemment n'en a pas encore.
      </p>
    );
  }

  const incomplet = anneesDispo < profondeurVisee - 0.5;
  const resume = `Cours de clôture ajusté du ${fmtDate(premier!)} au ${fmtDate(dernier![0])}, `
    + `dernière clôture ${dernier![1].toLocaleString("fr-FR", { maximumFractionDigits: precision })} dollars.`;

  return (
    <figure>
      <div className="mb-3 flex flex-wrap gap-1" role="group" aria-label="Période affichée">
        {PLAGES.map((p) => {
          const indisponible = !disponible(p.ans);
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setPlage(p.id)}
              disabled={indisponible}
              aria-pressed={plage === p.id}
              title={indisponible ? `Historique insuffisant : ${duree(anneesDispo)} disponible${anneesDispo < 2 ? "" : "s"}` : undefined}
              className={`rounded border px-3 py-1 text-sm disabled:opacity-40 ${plage === p.id ? "font-semibold" : ""}`}
            >
              {p.libelle}
            </button>
          );
        })}
      </div>

      <div ref={conteneur} role="img" aria-label={resume} className="h-72 w-full sm:h-96" />

      <figcaption className="mt-2 text-xs opacity-70">
        Clôture ajustée des dividendes et des divisions d'actions. Historique disponible
        du {fmtDate(premier!)} au {fmtDate(dernier![0])}.
        {incomplet && (
          <> Historique en cours d'approfondissement : {duree(anneesDispo)} disponible
            {anneesDispo < 2 ? "" : "s"} sur {profondeurVisee} visés, complété au fil des nuits.</>
        )}
      </figcaption>
    </figure>
  );
}
