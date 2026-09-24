"use client";

/**
 * Recherche de valeurs — champ à suggestions, conforme au motif ARIA « combobox ».
 *
 * Amélioration progressive : le composant est posé dans un <form action="/v">. Sans
 * JavaScript, ou avant que la liste ne soit chargée, Entrée soumet le ticker saisi
 * et l'aiguillage serveur fait le reste. Avec JavaScript, les suggestions s'ajoutent.
 */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { indexer, rechercher, type Entree, type LigneBrute, type Resultat } from "@/lib/recherche";

// Partagée entre toutes les instances du composant et conservée entre les navigations :
// la liste n'est téléchargée qu'une fois par session.
let chargement: Promise<Entree[]> | null = null;
function chargerIndex(): Promise<Entree[]> {
  chargement ??= fetch("/api/societes")
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<LigneBrute[]>;
    })
    .then(indexer)
    .catch((e) => {
      chargement = null;            // permettre une nouvelle tentative au prochain focus
      throw e;
    });
  return chargement;
}

export default function Recherche({ autoFocus = false }: { autoFocus?: boolean }) {
  const router = useRouter();
  const idListe = useId();
  const [valeur, setValeur] = useState("");
  const [index, setIndex] = useState<Entree[] | null>(null);
  const [erreur, setErreur] = useState(false);
  const [ouvert, setOuvert] = useState(false);
  const [actif, setActif] = useState(-1);
  const champ = useRef<HTMLInputElement>(null);

  const precharger = () => {
    if (index) return;
    chargerIndex().then(setIndex, () => setErreur(true));
  };

  useEffect(() => {
    if (autoFocus) precharger();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const resultats: Resultat[] = useMemo(
    () => (index && valeur.trim() ? rechercher(index, valeur) : []),
    [index, valeur],
  );
  const visible = ouvert && resultats.length > 0;

  const ouvrir = (ticker: string) => {
    setOuvert(false);
    router.push(`/v/${encodeURIComponent(ticker)}`);
  };

  const auClavier = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && resultats.length) {
      e.preventDefault();
      setOuvert(true);
      setActif((i) => (i + 1) % resultats.length);
    } else if (e.key === "ArrowUp" && resultats.length) {
      e.preventDefault();
      setActif((i) => (i <= 0 ? resultats.length - 1 : i - 1));
    } else if (e.key === "Escape") {
      setOuvert(false);
      setActif(-1);
    } else if (e.key === "Enter" && visible) {
      // Entrée sans sélection explicite : on ouvre le premier résultat. Sans
      // suggestion, on laisse le formulaire partir vers l'aiguillage serveur.
      e.preventDefault();
      ouvrir(resultats[actif >= 0 ? actif : 0].ticker);
    }
  };

  return (
    <form action="/v" method="get" role="search" className="relative">
      <label htmlFor={`${idListe}-champ`} className="sr-only">Rechercher une valeur par ticker ou par nom</label>
      <input
        ref={champ}
        id={`${idListe}-champ`}
        name="t"
        type="text"
        autoComplete="off"
        spellCheck={false}
        autoFocus={autoFocus}
        placeholder="Ticker ou nom de société"
        value={valeur}
        onFocus={precharger}
        onChange={(e) => {
          setValeur(e.target.value);
          setOuvert(true);
          setActif(-1);
        }}
        onBlur={() => setTimeout(() => setOuvert(false), 120)}
        onKeyDown={auClavier}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={visible}
        aria-controls={idListe}
        aria-activedescendant={visible && actif >= 0 ? `${idListe}-${actif}` : undefined}
        className="w-full rounded-sm border border-filet bg-papier px-3 py-2 placeholder:text-encre-seconde"
      />

      {visible && (
        <ul id={idListe} role="listbox" className="absolute z-10 mt-1 w-full overflow-hidden rounded-sm border border-filet bg-papier shadow-sm">
          {resultats.map((r, i) => (
            <li
              key={r.ticker}
              id={`${idListe}-${i}`}
              role="option"
              aria-selected={i === actif}
              onMouseDown={(e) => {
                e.preventDefault();        // conserver le focus : le clic l'emporte sur le blur
                ouvrir(r.ticker);
              }}
              onMouseEnter={() => setActif(i)}
              className={`cursor-pointer px-3 py-2 ${i === actif ? "bg-bande" : ""}`}
            >
              <span className="font-medium">{r.ticker}</span>{" "}
              <span className="text-encre-seconde">{r.nom}</span>
              {!r.univers && <span className="ml-2 text-sm text-encre-seconde">hors univers</span>}
              {r.autres.length > 0 && (
                <span className="block text-sm text-encre-seconde">
                  aussi coté {r.autres.slice(0, 3).join(", ")}
                  {r.autres.length > 3 && ` et ${r.autres.length - 3} autres lignes`}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {erreur && (
        <p className="mt-2 text-sm">
          Suggestions indisponibles. Saisissez un ticker complet et validez avec Entrée.
        </p>
      )}
    </form>
  );
}
