import Recherche from "./composants/Recherche";

export default function Accueil() {
  return (
    <main className="mx-auto max-w-xl px-6 py-28">
      <h1 className="text-[2.75rem] font-semibold leading-none tracking-tight">Analyse d'actions</h1>
      <p className="mt-4 text-encre-seconde">
        Les comptes déposés à la SEC par les sociétés cotées aux États-Unis, lus face à leur propre histoire.
      </p>
      <div className="mt-10">
        <Recherche autoFocus />
      </div>
    </main>
  );
}
