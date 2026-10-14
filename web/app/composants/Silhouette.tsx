/**
 * Silhouette de page pendant le chargement (étape 6).
 *
 * Une fiche met une à deux secondes à se construire. Dans l'application installée, un
 * écran figé ressemble à un plantage : la silhouette répond au toucher tout de suite.
 * Des blocs de la couleur des bandes du registre, sans animation si l'appareil la
 * refuse (globals.css).
 */
const Bloc = ({ className }: { className: string }) => <div className={`rounded-sm bg-bande ${className}`} />;

export default function Silhouette({ quoi }: { quoi: string }) {
  return (
    <main className="mx-auto max-w-3xl px-5 pb-16 pt-2 sm:px-8" aria-busy="true">
      <p className="sr-only" role="status">{quoi}</p>
      <div className="animate-pulse">
        <Bloc className="mt-12 h-11 w-40" />
        <Bloc className="mt-4 h-6 w-72 max-w-full" />
        <Bloc className="mt-6 h-4 w-full" />
        <Bloc className="mt-2 h-4 w-5/6" />
        <div className="mt-10 border-t border-filet">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="grid grid-cols-1 gap-2 border-b border-filet py-5 sm:grid-cols-[8.5rem_1fr]">
              <Bloc className="h-5 w-24" />
              <div><Bloc className="h-6 w-3/4" /><Bloc className="mt-2 h-4 w-1/2" /></div>
            </div>
          ))}
        </div>
        <Bloc className="mt-12 h-72 w-full" />
      </div>
    </main>
  );
}
