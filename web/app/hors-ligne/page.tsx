/** Page affichée par l'application installée quand le réseau manque (étape 6). */
export const dynamic = "force-static";

export default function HorsLigne() {
  return (
    <main className="mx-auto max-w-sm px-6 py-28">
      <h1 className="text-[2.25rem] font-semibold leading-none tracking-tight">Pas de connexion</h1>
      <p className="mt-4 leading-relaxed text-encre-seconde">
        Les comptes, les cours et les notes sont lus en direct : sans connexion, rien ne
        s&apos;affiche plutôt que des chiffres périmés. Réessayez une fois le réseau revenu.
      </p>
      {/* Un lien plutôt qu'un bouton en JavaScript : il recharge l'accueil par le réseau. */}
      <a href="/" className="mt-8 inline-block rounded-sm bg-encre px-4 py-2 text-papier">Réessayer</a>
    </main>
  );
}
