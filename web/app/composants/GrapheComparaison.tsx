"use client";

/**
 * Graphe de comparaison — étape 5.
 *
 * Deux cours ramenés à une base 100 au début de la période, et le S&P 500 en option.
 * Le rebasage est un choix d'affichage, pas une formule d'analyse : il ne fait que
 * diviser chaque série par sa première valeur de la période.
 *
 * Les séries se distinguent par le TRAIT, pas par la couleur : plein, tirets, pointillés
 * gris pour l'indice. Rien ne suggère laquelle « gagne », et la lecture tient en
 * impression noir et blanc comme pour un daltonien.
 *
 * Cours ajustés des seules divisions, comme sur la fiche, et S&P 500 de prix (^GSPC) :
 * on compare des cours à un cours, hors dividendes. Le rendement total, dividendes
 * compris, est dans le tableau sous le graphe.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType, createChart, LineSeries, LineStyle,
  type IChartApi, type ISeriesApi, type UTCTimestamp,
} from "lightweight-charts";

export type SerieComparee = { ticker: string; libelle: string; points: [string, number][] };

const PLAGES = [
  { id: "1a", libelle: "1 an", ans: 1 },
  { id: "3a", libelle: "3 ans", ans: 3 },
  { id: "5a", libelle: "5 ans", ans: 5 },
  { id: "10a", libelle: "10 ans", ans: 10 },
] as const;
type Plage = (typeof PLAGES)[number]["id"];
const TOLERANCE = 0.9;            // même règle que le graphe de la fiche
const AN = 365.25 * 86400;

const versTemps = (d: string) => {
  const [a, m, j] = d.split("-").map(Number);
  return (Date.UTC(a, m - 1, j) / 1000) as UTCTimestamp;
};
const pct = (v: number) =>
  `${v >= 0 ? "+" : "−"}${Math.abs(v).toLocaleString("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: 1 })} %`;

/** Série rebasée à 100 sur sa première séance postérieure à `depuis`. */
function rebaser(points: [string, number][], depuis: number) {
  const dedans = points.filter(([t, c]) => versTemps(t) >= depuis && c > 0);
  if (dedans.length === 0) return [];
  const base = dedans[0][1];
  return dedans.map(([t, c]) => ({ time: versTemps(t), value: (c / base) * 100 }));
}

export default function GrapheComparaison({ a, b, indice }: {
  a: SerieComparee; b: SerieComparee; indice: SerieComparee | null;
}) {
  const conteneur = useRef<HTMLDivElement>(null);
  const graphe = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Line">[]>([]);

  // Période commune : elle commence quand la plus jeune des deux séries commence.
  const debut = Math.max(versTemps(a.points[0]?.[0] ?? "2100-01-01"), versTemps(b.points[0]?.[0] ?? "2100-01-01"));
  const fin = Math.min(versTemps(a.points.at(-1)?.[0] ?? "1900-01-01"), versTemps(b.points.at(-1)?.[0] ?? "1900-01-01"));
  const anneesCommunes = (fin - debut) / AN;
  const disponible = (ans: number) => anneesCommunes >= ans * TOLERANCE;
  const defaut: Plage = disponible(5) ? "5a" : disponible(3) ? "3a" : "1a";
  const [plage, setPlage] = useState<Plage>(defaut);
  const [avecIndice, setAvecIndice] = useState(true);

  const depuis = useMemo(() => {
    const ans = PLAGES.find((p) => p.id === plage)!.ans;
    return Math.max(debut, fin - ans * AN);
  }, [plage, debut, fin]);
  const donnees = useMemo(() => ({
    a: rebaser(a.points, depuis),
    b: rebaser(b.points, depuis),
    i: indice ? rebaser(indice.points, depuis) : [],
  }), [a, b, indice, depuis]);

  useEffect(() => {
    const el = conteneur.current;
    if (!el) return;
    const jeton = (nom: string) => getComputedStyle(document.documentElement).getPropertyValue(nom).trim();
    const encre = jeton("--encre"), seconde = jeton("--encre-seconde");
    const bande = jeton("--bande"), fond = jeton("--papier");
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: fond },
        textColor: seconde,
        fontFamily: getComputedStyle(document.body).fontFamily,
        attributionLogo: true,
      },
      localization: { locale: "fr-FR" },
      grid: { vertLines: { visible: false }, horzLines: { color: bande } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, minBarSpacing: 0.05 },
    });
    const format = {
      type: "custom" as const, minMove: 0.1,
      formatter: (v: number) => v.toLocaleString("fr-FR", { maximumFractionDigits: 0 }),
    };
    const commun = { priceLineVisible: false, lastValueVisible: true, priceFormat: format };
    series.current = [
      chart.addSeries(LineSeries, { ...commun, color: encre, lineWidth: 2, lineStyle: LineStyle.Solid }),
      chart.addSeries(LineSeries, { ...commun, color: encre, lineWidth: 2, lineStyle: LineStyle.Dashed }),
      chart.addSeries(LineSeries, { ...commun, color: seconde, lineWidth: 1, lineStyle: LineStyle.Dotted }),
    ];
    graphe.current = chart;
    return () => {
      chart.remove();
      graphe.current = null;
      series.current = [];
    };
  }, []);

  useEffect(() => {
    const [sa, sb, si] = series.current;
    if (!sa) return;
    sa.setData(donnees.a);
    sb.setData(donnees.b);
    si.setData(avecIndice ? donnees.i : []);
    graphe.current?.timeScale().fitContent();
  }, [donnees, avecIndice]);

  const evolution = (d: { value: number }[]) => (d.length ? d[d.length - 1].value / 100 - 1 : null);
  const legende: [SerieComparee, { value: number }[], string][] = [
    [a, donnees.a, "border-t-2 border-encre"],
    [b, donnees.b, "border-t-2 border-dashed border-encre"],
    ...(indice && avecIndice ? [[indice, donnees.i, "border-t border-dotted border-encre-seconde"]] as
      [SerieComparee, { value: number }[], string][] : []),
  ];
  const resume = legende.map(([s, d]) => {
    const e = evolution(d);
    return `${s.libelle} ${e === null ? "sans cours" : pct(e * 100)}`;
  }).join(", ");

  return (
    <figure>
      <div className="mb-3 flex flex-wrap items-center gap-1" role="group" aria-label="Période affichée">
        {PLAGES.map((p) => {
          const indispo = !disponible(p.ans);
          return (
            <button key={p.id} type="button" disabled={indispo} aria-pressed={plage === p.id}
                    onClick={() => setPlage(p.id)}
                    title={indispo ? "Historique commun trop court" : undefined}
                    className={`rounded-sm px-3 py-1 text-sm ${plage === p.id ? "bg-encre text-papier"
                      : indispo ? "text-filet" : "text-encre-seconde underline-offset-4 hover:underline"}`}>
              {p.libelle}
            </button>
          );
        })}
        {indice && (
          <label className="ml-auto flex items-center gap-2 text-sm text-encre-seconde">
            <input type="checkbox" checked={avecIndice} onChange={(e) => setAvecIndice(e.target.checked)} />
            S&amp;P 500
          </label>
        )}
      </div>
      <div ref={conteneur} className="h-72 w-full sm:h-80" role="img"
           aria-label={`Évolution des cours sur la période, base 100 : ${resume}.`} />
      <figcaption className="mt-3 text-sm text-encre-seconde">
        <ul className="flex flex-wrap gap-x-6 gap-y-1">
          {legende.map(([s, d, trait]) => {
            const e = evolution(d);
            return (
              <li key={s.ticker} className="flex items-center gap-2">
                <span aria-hidden className={`inline-block w-6 ${trait}`} />
                <span className="text-encre">{s.libelle}</span>
                <span className={`tabular-nums ${e !== null && e < 0 ? "text-perte" : ""}`}>
                  {e === null ? "sans cours" : pct(e * 100)}
                </span>
              </li>
            );
          })}
        </ul>
        <p className="mt-2">
          Cours ramenés à 100 au début de la période, hors dividendes. Le rendement total,
          dividendes réinvestis, figure dans le tableau ci-dessous.
        </p>
      </figcaption>
    </figure>
  );
}
