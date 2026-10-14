"""
comparables.py — Sociétés comparables (étape 3d).

Pour chaque société de l'univers, les cinq sociétés les plus proches, calculées chaque
soir avec les percentiles et les notes. L'interface ne fait que lire.

Candidates : les sociétés de l'univers de la même industrie ET du même profil (une
banque ne se compare pas à une foncière). Sous six sociétés dans l'industrie, on élargit
au secteur. Une société n'est jamais sa propre comparable, ni ses autres catégories
d'actions (même CIK : GOOG pour GOOGL).

Proximité : une distance sur quatre critères, décidée le 14 octobre 2026.
  - la TAILLE, en ordre de grandeur de capitalisation, qui pèse autant que les trois
    autres réunis : Apple ne se compare pas à une petite société de son industrie ;
  - le modèle économique (marge brute), la rentabilité (marge opérationnelle) et la
    croissance (chiffre d'affaires sur 3 ans), mesurés par leur rang dans le groupe de
    candidates : un écart de marge brute de 10 points n'a pas le même sens chez les
    éditeurs de logiciels et chez les distributeurs.
Une donnée manquante ne disqualifie pas, mais ne rapproche pas non plus : l'écart sur ce
critère vaut l'écart MOYEN entre deux sociétés prises au hasard (1/6 au carré, pour deux
rangs uniformes). Calculer la distance sur les seuls critères connus faisait d'une
société sans données la voisine idéale de toutes celles de sa taille.
"""
from __future__ import annotations

import math
from collections import defaultdict

NOMBRE = 5
GROUPE_MIN = 6                  # sous six sociétés dans l'industrie : le secteur
CRITERES = ("marge_brute", "marge_operationnelle", "cagr_ca_3a")
POIDS_TAILLE = float(len(CRITERES))     # autant que les trois autres réunis
ECHELLE_TAILLE = 1.5            # un écart d'un facteur ~30 de capitalisation = écart maximal
NON_CLASSE = "Non classé"
ECART_INCONNU = 1 / 6           # écart quadratique moyen entre deux rangs tirés au hasard


def _rangs(valeurs: dict) -> dict:
    """{cid: rang 0-1} à rang moyen, sur les seules valeurs présentes."""
    tri = sorted((v, cid) for cid, v in valeurs.items() if v is not None)
    n = len(tri)
    if n < 2:
        return {}
    out, i = {}, 0
    while i < n:
        j = i
        while j + 1 < n and tri[j + 1][0] == tri[i][0]:
            j += 1
        r = (i + j) / 2 / (n - 1)
        for k in range(i, j + 1):
            out[tri[k][1]] = r
        i = j + 1
    return out


def _distance(a: dict, b: dict) -> float:
    """Distance 0-1 entre deux sociétés d'un même groupe."""
    ds = min(abs(a["taille"] - b["taille"]) / ECHELLE_TAILLE, 1.0)
    somme = POIDS_TAILLE * ds * ds
    for c in CRITERES:
        ra, rb = a["rangs"].get(c), b["rangs"].get(c)
        somme += (ra - rb) ** 2 if ra is not None and rb is not None else ECART_INCONNU
    return math.sqrt(somme / (POIDS_TAILLE + len(CRITERES)))


def comparables(societes: list) -> list:
    """
    `societes` : (company_id, cik, profil, secteur, industrie, capitalisation,
                  {critère: valeur}) — sociétés de l'univers.
    Renvoie (company_id, rang 1-5, comparable_id, distance, niveau).
    """
    fiches = {}
    for cid, cik, profil, secteur, industrie, cap, valeurs in societes:
        if not cap or cap <= 0 or not secteur or secteur == NON_CLASSE:
            continue
        fiches[cid] = {"cik": cik, "profil": profil, "secteur": secteur,
                       "industrie": industrie, "taille": math.log10(cap),
                       "valeurs": valeurs}

    par_industrie, par_secteur = defaultdict(list), defaultdict(list)
    for cid, f in fiches.items():
        par_industrie[(f["profil"], f["industrie"])].append(cid)
        par_secteur[(f["profil"], f["secteur"])].append(cid)

    # Rangs calculés une fois par groupe : un même groupe sert à tous ses membres.
    rangs_groupe: dict = {}

    def rangs_de(cle, membres):
        if cle not in rangs_groupe:
            rangs_groupe[cle] = {c: _rangs({m: fiches[m]["valeurs"].get(c) for m in membres})
                                 for c in CRITERES}
        return rangs_groupe[cle]

    out = []
    for cid, f in fiches.items():
        membres = par_industrie[(f["profil"], f["industrie"])]
        niveau = "industrie"
        if len(membres) < GROUPE_MIN:
            membres, niveau = par_secteur[(f["profil"], f["secteur"])], "secteur"
        cle = (niveau, f["profil"], f["industrie"] if niveau == "industrie" else f["secteur"])
        rg = rangs_de(cle, membres)
        moi = {"taille": f["taille"], "rangs": {c: rg[c].get(cid) for c in CRITERES}}
        candidates = []
        for m in membres:
            if m == cid or (f["cik"] is not None and fiches[m]["cik"] == f["cik"]):
                continue
            autre = {"taille": fiches[m]["taille"], "rangs": {c: rg[c].get(m) for c in CRITERES}}
            candidates.append((_distance(moi, autre), m))
        # Égalités départagées par l'identifiant : le résultat ne dépend pas de l'ordre
        # de lecture, sinon la garde différentielle réécrirait des lignes chaque soir.
        candidates.sort()
        retenues, ciks = [], set()
        for d, m in candidates:
            # Deux catégories d'actions d'une même société ne prennent qu'une place.
            ck = fiches[m]["cik"]
            if ck is not None and ck in ciks:
                continue
            ciks.add(ck)
            retenues.append((d, m))
            if len(retenues) == NOMBRE:
                break
        for rang, (d, m) in enumerate(retenues, start=1):
            out.append((cid, rang, m, round(d, 4), niveau))
    return out
