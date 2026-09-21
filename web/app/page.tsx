import Recherche from "./composants/Recherche";

export default function Accueil() {
  return (
    <main className="mx-auto max-w-xl px-6 py-24">
      <h1 className="text-2xl font-semibold">Analyse d'actions</h1>
      <div className="mt-8">
        <Recherche autoFocus />
      </div>
    </main>
  );
}
