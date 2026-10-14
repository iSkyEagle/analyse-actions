"use client";

/** Ajoute la valeur à la liste suivie de l'appareil, affichée sur l'accueil (étape 6). */
import { useEffect, useState } from "react";
import { basculer, EVENEMENT, lireSuivies, MAX_SUIVIES } from "@/lib/suivies";

export default function BoutonSuivre({ ticker }: { ticker: string }) {
  // null avant lecture : le rendu serveur ne connaît pas la liste de l'appareil.
  const [suivie, setSuivie] = useState<boolean | null>(null);
  const [pleine, setPleine] = useState(false);
  useEffect(() => {
    const maj = () => {
      const l = lireSuivies();
      setSuivie(l.includes(ticker));
      setPleine(l.length >= MAX_SUIVIES);
    };
    maj();
    window.addEventListener(EVENEMENT, maj);
    return () => window.removeEventListener(EVENEMENT, maj);
  }, [ticker]);

  if (suivie === null) return null;
  return (
    <button type="button" onClick={() => setSuivie(basculer(ticker))} aria-pressed={suivie}
            disabled={!suivie && pleine} title={!suivie && pleine ? `Liste pleine : ${MAX_SUIVIES} valeurs au plus` : undefined}
            className={`rounded-sm border px-3 py-1 text-sm ${suivie ? "border-encre bg-encre text-papier" : "border-filet"}`}>
      {suivie ? "Suivie" : "Suivre cette valeur"}
    </button>
  );
}
