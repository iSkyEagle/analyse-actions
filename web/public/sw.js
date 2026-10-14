/*
 * Service worker (étape 6) — le strict nécessaire pour l'application installée.
 *
 * Décidé le 14 octobre 2026 : hors connexion, une page qui le dit, et rien d'autre.
 * Aucune donnée n'est mise en cache : une fiche lue hors ligne serait une fiche
 * périmée présentée comme actuelle. Les pages passent toujours par le réseau ; seule
 * la page « hors ligne » et ses icônes sont gardées, pour s'afficher sans réseau.
 */
const CACHE = "hors-ligne-v1";
const A_GARDER = ["/hors-ligne", "/icone-192.png"];

// La page hors ligne a besoin de sa feuille de style, servie sous /_next/static/ avec
// un nom qui change à chaque déploiement : on la relève dans la page elle-même.
async function garder() {
  const c = await caches.open(CACHE);
  await c.addAll(A_GARDER);
  const page = await c.match("/hors-ligne");
  const html = page ? await page.text() : "";
  const styles = [...html.matchAll(/href="(\/_next\/static\/[^"]+\.css)"/g)].map((m) => m[1]);
  await c.addAll(styles);
}

self.addEventListener("install", (e) => {
  e.waitUntil(garder().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((cles) => Promise.all(cles.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (e) => {
  // Seuls les échecs réseau sont rattrapés : navigations vers la page hors ligne,
  // feuilles de style vers leur copie gardée. Tout le reste passe tel quel.
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match("/hors-ligne")));
  } else if (new URL(e.request.url).pathname.startsWith("/_next/static/")) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
  }
});
