import type { NextConfig } from "next";

const config: NextConfig = {
  // `pg` utilise des modules natifs de Node : on l'exclut du bundling serveur pour
  // qu'il soit chargé tel quel à l'exécution, au lieu d'être réécrit par le bundler.
  serverExternalPackages: ["pg"],
};

export default config;
