/**
 * Accueil de l'étape 1 : une saisie de ticker, sans recherche. La recherche par nom
 * et l'autocomplétion arrivent à l'étape 2.
 */
export default function Accueil() {
  return (
    <main className="mx-auto max-w-xl px-6 py-24">
      <h1 className="text-2xl font-semibold">Analyse d'actions</h1>
      <form action="/v" method="get" className="mt-8 flex gap-2">
        <label htmlFor="t" className="sr-only">Ticker</label>
        <input
          id="t" name="t" required autoFocus placeholder="MSFT"
          className="flex-1 rounded border px-3 py-2 uppercase"
        />
        <button type="submit" className="rounded border px-4 py-2">Ouvrir la fiche</button>
      </form>
    </main>
  );
}
