/**
 * Garde d'accès (étape 6) : toute page demande le cookie d'accès, sauf la page de
 * connexion, les fichiers de l'application installable et les ressources statiques.
 * Sans ACCES_MOT_DE_PASSE, le site est ouvert.
 */
import { NextResponse, type NextRequest } from "next/server";
import { COOKIE_ACCES, egal, jeton, motDePasse } from "@/lib/acces";

export function proxy(req: NextRequest) {
  const mdp = motDePasse();
  if (!mdp) return NextResponse.next();
  const recu = req.cookies.get(COOKIE_ACCES)?.value;
  if (recu && egal(recu, jeton(mdp))) return NextResponse.next();

  // Une route d'API répond 401 plutôt que de renvoyer une page HTML de connexion.
  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ erreur: "accès refusé" }, { status: 401 });
  }
  const url = req.nextUrl.clone();
  url.pathname = "/connexion";
  url.search = "";
  url.searchParams.set("suite", req.nextUrl.pathname + req.nextUrl.search);
  return NextResponse.redirect(url);
}

export const config = {
  // Exclus : la connexion elle-même, les fichiers Next, l'application installable
  // (manifeste, icônes, service worker, page hors ligne) et robots.txt.
  matcher: [
    "/((?!connexion|api/connexion|_next/|manifest\\.webmanifest|icone-|icon\\.png|apple-icon|favicon\\.ico|sw\\.js|hors-ligne|robots\\.txt).*)",
  ],
};
