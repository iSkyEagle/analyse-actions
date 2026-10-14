"use client";

/** Enregistre le service worker, en production seulement (étape 6). */
import { useEffect } from "react";

export default function ServiceWorker() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register("/sw.js").catch(() => { /* sans effet sur le site */ });
  }, []);
  return null;
}
