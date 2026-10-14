/**
 * Vérification du mot de passe (étape 6). Formulaire HTML classique : la connexion
 * marche sans JavaScript, et le navigateur peut proposer d'enregistrer le mot de passe.
 */
import { NextResponse, type NextRequest } from "next/server";
import { COOKIE_ACCES, DUREE_ACCES, egal, jeton, motDePasse, suiteSure } from "@/lib/acces";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const form = await req.formData();
  const saisi = String(form.get("mot_de_passe") ?? "");
  const suite = suiteSure(String(form.get("suite") ?? ""));
  const mdp = motDePasse();
  const base = new URL(req.url);

  if (!mdp) return NextResponse.redirect(new URL(suite, base), 303);
  if (!egal(jeton(saisi), jeton(mdp))) {
    // Une demi-seconde par essai manqué : sans effet pour un humain, dissuasif pour un
    // robot qui tenterait des milliers de mots de passe.
    await new Promise((r) => setTimeout(r, 500));
    const retour = new URL("/connexion", base);
    retour.searchParams.set("suite", suite);
    retour.searchParams.set("erreur", "1");
    return NextResponse.redirect(retour, 303);
  }
  const rep = NextResponse.redirect(new URL(suite, base), 303);
  rep.cookies.set(COOKIE_ACCES, jeton(mdp), {
    httpOnly: true, secure: base.protocol === "https:", sameSite: "lax", path: "/", maxAge: DUREE_ACCES,
  });
  return rep;
}
