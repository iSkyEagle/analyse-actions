/**
 * Valeurs suivies d'un appareil (étape 6) : /api/suivies?t=MSFT,KO. La liste vit dans
 * le navigateur ; le serveur ne fait que relire leurs notes du jour.
 */
import { NextResponse, type NextRequest } from "next/server";
import { suivies } from "@/lib/queries";
import { MAX_SUIVIES } from "@/lib/suivies";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const t = (req.nextUrl.searchParams.get("t") ?? "")
    .split(",").map((x) => x.trim().toUpperCase()).filter((x) => /^[A-Z0-9.\-^]{1,12}$/.test(x))
    .slice(0, MAX_SUIVIES);
  const lignes = await suivies(t);
  // Dans l'ordre de la liste de l'appareil, pas dans celui de la base.
  const ordre = new Map(t.map((x, i) => [x, i]));
  lignes.sort((a, b) => (ordre.get(a.ticker) ?? 0) - (ordre.get(b.ticker) ?? 0));
  return NextResponse.json(lignes, { headers: { "Cache-Control": "private, max-age=300" } });
}
