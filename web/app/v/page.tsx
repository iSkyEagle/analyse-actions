import { redirect } from "next/navigation";

/** Réception du formulaire d'accueil : /v?t=msft -> /v/MSFT. */
export default async function Aiguillage({ searchParams }: { searchParams: Promise<{ t?: string }> }) {
  const { t } = await searchParams;
  const ticker = (t ?? "").trim().toUpperCase();
  redirect(ticker ? `/v/${encodeURIComponent(ticker)}` : "/");
}
