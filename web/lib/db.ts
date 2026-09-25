/**
 * lib/db.ts — Accès Postgres, côté serveur uniquement.
 *
 * Le Data API Supabase est désactivé : aucune requête ne part du navigateur. Toute
 * lecture passe par ce module, importé exclusivement depuis des composants serveur
 * ou des routes d'API. L'import de `server-only` fait échouer la compilation si un
 * composant client tente de l'importer — la chaîne de connexion ne peut pas fuir
 * dans le bundle navigateur par erreur.
 */
import "server-only";
import { Pool, types } from "pg";

// Les colonnes `date` sont rendues telles quelles, en chaîne 'AAAA-MM-JJ'. Par défaut,
// pg les convertit en objet Date à minuit HEURE LOCALE du serveur : sur Vercel (UTC)
// un 2026-03-31 reste le 31, mais en local à Paris il devient le 30 mars à 22h, et
// une date de clôture d'exercice se décale d'un jour selon la machine.
types.setTypeParser(1082, (v: string) => v);

/**
 * Un seul pool par instance, réutilisé entre les invocations.
 *
 * En serverless, chaque instance de fonction garde son état de module tant qu'elle
 * reste chaude. Recréer un pool à chaque requête ouvrirait une connexion par appel
 * et saturerait le pooler — l'ECHECKOUTTIMEOUT déjà rencontré en phase 1.
 * En développement, le rechargement à chaud réévalue ce module à chaque
 * modification : on accroche le pool à `globalThis` pour qu'il survive.
 *
 * `max: 3` : Vercel peut faire tourner plusieurs instances en parallèle, chacune avec
 * son pool. Le tier gratuit plafonne à 60 connexions, partagées avec l'ingestion.
 *
 * Mode transaction du pooler (port 6543) : node-postgres envoie les requêtes
 * paramétrées comme instructions préparées ANONYMES, que Supavisor accepte. Ne jamais
 * passer d'option `name` à une requête — une instruction préparée nommée est liée à
 * une connexion serveur que le mode transaction ne garantit pas de retrouver.
 */
// Accroché à globalThis dans tous les environnements : en développement pour
// survivre au rechargement à chaud, en production pour qu'une instance chaude le
// réutilise d'une invocation à l'autre.
const globalPourPg = globalThis as unknown as { pgPool?: Pool };

function creerPool(): Pool {
  const url = process.env.DATABASE_URL;
  if (!url) {
    throw new Error(
      "DATABASE_URL absente. En local : web/.env.local ; sur Vercel : variables d'environnement du projet.",
    );
  }
  return new Pool({
    connectionString: url,
    max: 3,
    idleTimeoutMillis: 10_000,
    connectionTimeoutMillis: 8_000,
    // Chiffrement actif, sans épinglage de l'autorité de certification Supabase —
    // absente du magasin de confiance par défaut de Node. Ajouter son certificat
    // (Settings -> Database -> SSL) permettrait de passer à une vérification complète.
    ssl: url.includes("localhost") || url.includes("host=/") ? false : { rejectUnauthorized: false },
  });
}

/**
 * Création PARESSEUSE, à la première requête. `next build` importe chaque page pour en
 * collecter la configuration : un pool créé dès l'import exigerait DATABASE_URL au
 * moment du build, et le ferait échouer partout où la variable n'est définie qu'à
 * l'exécution. Constaté à la première compilation.
 */
function pool(): Pool {
  if (!globalPourPg.pgPool) globalPourPg.pgPool = creerPool();
  return globalPourPg.pgPool;
}

/** Exécute une requête paramétrée et renvoie les lignes typées. */
export async function query<T>(texte: string, params: unknown[] = []): Promise<T[]> {
  const res = await pool().query(texte, params);
  return res.rows as T[];
}
