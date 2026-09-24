/**
 * Tendance sur les derniers exercices — un trait, sans axe ni légende.
 *
 * Composant serveur : du SVG pur, rien à charger dans le navigateur. Le mémoire
 * demande « la tendance des marges sur 5 ans » : c'est la forme qui compte ici, pas
 * la lecture d'une valeur précise, que donne déjà la colonne chiffrée à côté.
 *
 * Accessible : le SVG porte la liste des valeurs en clair.
 */
import { metrique } from "@/lib/format";

export default function Tendance({
  points,
  format,
  libelle,
}: {
  points: { period_end: string; value: number }[];
  format: string;
  libelle: string;
}) {
  if (points.length < 2) return <span className="inline-block w-20" aria-hidden />;

  const L = 80, H = 22, M = 3;
  const vals = points.map((p) => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const etendue = max - min || Math.abs(max) || 1;
  const x = (i: number) => M + (i * (L - 2 * M)) / (points.length - 1);
  const y = (v: number) => H - M - ((v - min) / etendue) * (H - 2 * M);
  const trace = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const dernier = points[points.length - 1];

  const resume = `${libelle}, ${points[0].period_end.slice(0, 4)} à ${dernier.period_end.slice(0, 4)} : `
    + points.map((p) => metrique(p.value, format)).join(", ");

  return (
    <svg width={L} height={H} viewBox={`0 0 ${L} ${H}`} role="img" aria-label={resume} className="shrink-0">
      {/* ligne de zéro si la série le traverse : une marge qui passe en perte se voit */}
      {min < 0 && max > 0 && (
        <line x1={M} x2={L - M} y1={y(0)} y2={y(0)} stroke="var(--filet)" strokeWidth="1" />
      )}
      <polyline points={trace} fill="none" stroke="var(--encre)" strokeWidth="1.5"
                strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(points.length - 1)} cy={y(dernier.value)} r="2.5" fill="var(--encre)" />
    </svg>
  );
}
