import Link from "next/link";

export default function Introuvable() {
  return (
    <main className="mx-auto max-w-xl px-6 py-24">
      <h1 className="text-2xl font-semibold">Ticker inconnu</h1>
      <p className="mt-4">
        Ce ticker ne figure pas parmi les valeurs suivies. Seules les actions ordinaires
        cotées au Nasdaq, au NYSE ou au CBOE, avec un dépôt EDGAR de moins de six mois,
        sont dans la base.
      </p>
      <Link href="/" className="mt-6 inline-block underline">Saisir un autre ticker</Link>
    </main>
  );
}
