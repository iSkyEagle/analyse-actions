"use client";

/**
 * En-tête commun (étape 6) : retour, nom du site vers l'accueil, recherche.
 *
 * Installée sur iPhone, l'application s'ouvre sans barre de navigateur, donc sans
 * bouton « précédent » : ce retour est le seul moyen de revenir d'une comparaison à la
 * fiche. Ouverte directement sur une page (lien partagé, lancement), il n'y a pas
 * d'historique : le retour mène alors à l'accueil.
 *
 * Collé en haut de l'écran : la recherche reste à portée de pouce sur une fiche longue.
 */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import Recherche from "./Recherche";

export default function EnTete() {
  const router = useRouter();
  const chemin = usePathname();
  const accueil = chemin === "/";
  // L'accueil a déjà son titre et sa recherche ; la connexion et la page hors ligne
  // n'ont rien vers quoi naviguer.
  if (accueil || chemin === "/connexion" || chemin === "/hors-ligne") return null;
  const retour = () => (window.history.length > 1 ? router.back() : router.push("/"));
  return (
    <header className="sticky top-0 z-20 border-b border-filet bg-papier/95 backdrop-blur-sm">
      <div className="mx-auto flex max-w-3xl items-center gap-3 px-5 py-2.5 sm:gap-5 sm:px-8">
        {(
          // 44 px de côté au moins : la cible tactile recommandée sur téléphone.
          <button type="button" onClick={retour} aria-label="Retour à la page précédente"
                  className="-ml-3 flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1 rounded-sm px-2 text-encre-seconde hover:text-encre">
            <span aria-hidden className="text-2xl leading-none">‹</span>
            <span className="hidden sm:inline">Retour</span>
          </button>
        )}
        <Link href="/" className="shrink-0 font-semibold">
          <span className="sm:hidden">Analyse</span>
          <span className="hidden sm:inline">Analyse d&apos;actions</span>
        </Link>
        <div className="min-w-0 flex-1"><Recherche /></div>
      </div>
    </header>
  );
}
