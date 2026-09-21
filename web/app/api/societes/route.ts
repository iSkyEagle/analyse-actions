/**
 * GET /api/societes — la liste compacte servie au composant de recherche.
 *
 * Route DYNAMIQUE, cache confié au CDN. Un `revalidate` ferait de cette route une
 * page statique, que Next.js exécute au moment du build pour en figer le résultat :
 * la compilation interrogerait la base, et échouerait partout où DATABASE_URL n'est
 * pas définie à cet instant. Constaté au premier build.
 *
 * `s-maxage` est l'en-tête que lit le CDN de Vercel : la liste est servie depuis le
 * cache pendant une heure, puis rafraîchie en arrière-plan. La base est sollicitée
 * au plus une fois par heure, jamais pendant la saisie ni pendant le build.
 */
import { listeSocietes } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET() {
  const lignes = await listeSocietes();
  return Response.json(lignes, {
    headers: {
      "Cache-Control": "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
