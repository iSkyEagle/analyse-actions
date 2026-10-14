/**
 * Page de connexion (étape 6) : un seul champ, le mot de passe partagé. L'appareil
 * s'en souvient 90 jours.
 */
import type { Metadata } from "next";
import { suiteSure } from "@/lib/acces";

export const metadata: Metadata = { title: "Connexion — Analyse d'actions" };

export default async function Connexion({ searchParams }: {
  searchParams: Promise<{ suite?: string; erreur?: string }>;
}) {
  const { suite, erreur } = await searchParams;
  return (
    <main className="mx-auto max-w-sm px-6 py-28">
      <h1 className="text-[2.25rem] font-semibold leading-none tracking-tight">Analyse d&apos;actions</h1>
      <p className="mt-4 text-encre-seconde">Outil privé. Le mot de passe est demandé une fois par appareil.</p>
      <form action="/api/connexion" method="post" className="mt-10">
        <input type="hidden" name="suite" value={suiteSure(suite)} />
        {/* Un champ « utilisateur » caché : les gestionnaires de mots de passe en ont
            besoin pour proposer l'enregistrement. */}
        <input type="text" name="utilisateur" autoComplete="username" value="analyse-actions" readOnly hidden />
        <label htmlFor="mdp" className="block text-sm text-encre-seconde">Mot de passe</label>
        <input id="mdp" name="mot_de_passe" type="password" required autoFocus autoComplete="current-password"
               aria-invalid={erreur ? true : undefined} aria-describedby={erreur ? "mdp-erreur" : undefined}
               className="mt-1 w-full rounded-sm border border-filet bg-papier px-3 py-2" />
        {erreur && (
          <p id="mdp-erreur" role="alert" className="mt-2 border-l-2 border-encre pl-3 text-sm">
            Mot de passe incorrect.
          </p>
        )}
        <button type="submit" className="mt-5 rounded-sm bg-encre px-4 py-2 text-papier">Entrer</button>
      </form>
    </main>
  );
}
