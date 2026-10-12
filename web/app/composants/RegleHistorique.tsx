/**
 * Règle historique — l'élément fort de la direction visuelle, et le seul.
 *
 * Situe le multiple du jour face à l'histoire de la MÊME entreprise sur 5 ans :
 *   - le trait fin couvre l'étendue, du minimum au maximum ;
 *   - la bande, du 10e au 90e centile : là où le multiple a passé 80 % des séances ;
 *   - le trait vertical marque la médiane ;
 *   - le point plein, aujourd'hui.
 *
 * Aucune couleur ne juge : un PER au bas de sa fourchette n'est pas « vert », il est
 * simplement bas. La bande prend la couleur du filet, et non le vert comptable des
 * lignes, qui la rendrait invisible une ligne sur deux.
 *
 * Composant serveur : du SVG pur. L'étiquette accessible donne les chiffres en clair.
 */
import { metrique } from "@/lib/format";

export type Resume = {
  p10: number | null;
  mediane: number | null;
  p90: number | null;
  mini: number | null;
  maxi: number | null;
  couverture: number;
};

/** La règle n'a de sens que sur une distribution réelle, couverte au moins à moitié. */
export function regleAffichable(r: Resume | undefined, actuelle: number | null): r is Resume {
  return !!r && actuelle !== null && r.couverture >= 0.5 && r.mediane !== null
    && r.mini !== null && r.maxi !== null && r.p10 !== null && r.p90 !== null && r.maxi > r.mini;
}

export default function RegleHistorique({
  actuelle,
  resume,
  format,
  libelle,
}: {
  actuelle: number;
  resume: Resume;
  format: string;
  libelle: string;
}) {
  const L = 160, H = 22, M = 5;
  const { mini, maxi, p10, p90, mediane } = resume as Required<{ [K in keyof Resume]: number }>;
  // Le point du jour peut sortir de l'étendue : le résumé est hebdomadaire, le cours
  // quotidien. L'échelle s'élargit alors pour le montrer, au lieu de le tronquer.
  const lo = Math.min(mini, actuelle), hi = Math.max(maxi, actuelle);
  const x = (v: number) => M + ((v - lo) / (hi - lo || 1)) * (L - 2 * M);
  const y = H / 2;
  const f = (v: number) => metrique(v, format);

  const etiquette = `${libelle} : ${f(actuelle)} aujourd'hui. Sur 5 ans, de ${f(mini)} à ${f(maxi)}, `
    + `médiane ${f(mediane)} ; 80 % des séances entre ${f(p10)} et ${f(p90)}.`;

  return (
    <svg width={L} height={H} viewBox={`0 0 ${L} ${H}`} role="img" aria-label={etiquette}
         className="block max-w-full shrink-0">
      <title>{etiquette}</title>
      <line x1={x(mini)} x2={x(maxi)} y1={y} y2={y} stroke="var(--encre-seconde)" strokeWidth="1" />
      <line x1={x(mini)} x2={x(mini)} y1={y - 3} y2={y + 3} stroke="var(--encre-seconde)" strokeWidth="1" />
      <line x1={x(maxi)} x2={x(maxi)} y1={y - 3} y2={y + 3} stroke="var(--encre-seconde)" strokeWidth="1" />
      <rect x={x(p10)} y={y - 4} width={Math.max(x(p90) - x(p10), 1)} height="8" fill="var(--filet)" />
      <line x1={x(mediane)} x2={x(mediane)} y1={y - 7} y2={y + 7} stroke="var(--encre)" strokeWidth="1.5" />
      <circle cx={x(actuelle)} cy={y} r="4" fill="var(--encre)" />
    </svg>
  );
}
