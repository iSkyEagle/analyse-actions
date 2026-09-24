"""
metriques.py — Métriques dérivées : configuration et formules, en un seul fichier.

Règle du mémoire : « la configuration des métriques vit dans le code, lisible d'un coup
d'œil dans un seul fichier », et « les métriques dérivées sont calculées à l'ingestion,
jamais à l'affichage ». Ce module est l'unique implémentation : la fiche, la
comparaison, le screener et les percentiles de phase 3 lisent tous son résultat.

Règle 2 du mémoire, appliquée partout : un indicateur non applicable renvoie une
RAISON, jamais 0. Une société en perte n'a pas un PER mauvais, elle n'a pas de PER.

Étape 4a : aucun multiple historique. Tout ce qui est calculé ici repose soit sur des
totaux (chiffre d'affaires, résultat, flux), insensibles aux divisions d'actions, soit
sur la situation du jour. Les exceptions sont signalées là où elles se trouvent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from statistics import pstdev
from typing import Optional

STD, FIN, REIT = "standard", "financial", "reit"
TOUS = (STD, FIN, REIT)


# =============================================================================
# CONFIGURATION — lue par l'interface pour l'ordre, les libellés et les formats
# =============================================================================
@dataclass(frozen=True)
class Metrique:
    id: str
    libelle: str
    axe: str                    # valorisation | qualite | croissance | dilution | risque
    format: str                 # multiple | pourcentage | trimestres
    profils: tuple = TOUS
    serie_annuelle: bool = False
    note: str = ""
    # Rouge si négatif : réservé aux mesures de RÉSULTAT (marges, rentabilité). Une
    # baisse du nombre d'actions ou un écart au plus haut sont négatifs sans être des
    # pertes : les colorer ferait porter un jugement que le mémoire exclut.
    perte: bool = False


METRIQUES = [
    # --- Valorisation : situation du jour -----------------------------------------
    Metrique("per", "PER", "valorisation", "multiple",
             note="capitalisation / résultat net des 12 derniers mois"),
    Metrique("prix_ventes", "Prix sur ventes", "valorisation", "multiple"),
    Metrique("prix_valeur_comptable", "Prix sur valeur comptable", "valorisation", "multiple"),
    Metrique("ve_ebitda", "Valeur d'entreprise sur EBITDA", "valorisation", "multiple", (STD, REIT)),
    Metrique("ve_fcf", "Valeur d'entreprise sur flux de trésorerie libre", "valorisation", "multiple", (STD, REIT)),
    Metrique("rendement_dividende", "Rendement du dividende", "valorisation", "pourcentage"),
    Metrique("taux_distribution", "Taux de distribution", "valorisation", "pourcentage"),
    Metrique("peg_historique", "PER sur croissance passée", "valorisation", "multiple",
             note="PER / croissance annuelle du résultat net sur 5 ans. Variante calculable du "
                  "PEG, qui exige des prévisions d'analystes que nous n'avons pas."),

    # --- Qualité ------------------------------------------------------------------
    Metrique("marge_brute", "Marge brute", "qualite", "pourcentage", (STD,), True, perte=True),
    Metrique("marge_operationnelle", "Marge opérationnelle", "qualite", "pourcentage", (STD, REIT), True, perte=True),
    Metrique("marge_nette", "Marge nette", "qualite", "pourcentage", TOUS, True, perte=True),
    Metrique("roe", "Rentabilité des capitaux propres", "qualite", "pourcentage", TOUS, True, perte=True),
    Metrique("roic", "Rentabilité des capitaux investis", "qualite", "pourcentage", (STD, REIT), True, perte=True),
    Metrique("dette_nette_ebitda", "Dette nette sur EBITDA", "qualite", "multiple", (STD, REIT), True),
    Metrique("couverture_interets", "Couverture des intérêts", "qualite", "multiple", (STD, REIT), True),
    Metrique("ratio_liquidite", "Actif courant sur passif courant", "qualite", "multiple", (STD,), True),

    # --- Croissance : totaux, donc insensibles aux divisions ------------------------
    Metrique("cagr_ca_3a", "Chiffre d'affaires, 3 ans", "croissance", "pourcentage"),
    Metrique("cagr_ca_5a", "Chiffre d'affaires, 5 ans", "croissance", "pourcentage"),
    Metrique("cagr_rn_3a", "Résultat net, 3 ans", "croissance", "pourcentage"),
    Metrique("cagr_rn_5a", "Résultat net, 5 ans", "croissance", "pourcentage"),
    Metrique("cagr_fcf_5a", "Flux de trésorerie libre, 5 ans", "croissance", "pourcentage", (STD, REIT)),
    Metrique("croissance_trimestre", "Dernier trimestre sur un an", "croissance", "pourcentage"),

    # --- Dilution -----------------------------------------------------------------
    Metrique("cagr_actions_1a", "Nombre d'actions, 1 an", "dilution", "pourcentage"),
    Metrique("cagr_actions_3a", "Nombre d'actions, 3 ans", "dilution", "pourcentage"),
    Metrique("cagr_actions_5a", "Nombre d'actions, 5 ans", "dilution", "pourcentage"),
    Metrique("sbc_ventes", "Rémunération en actions sur ventes", "dilution", "pourcentage", TOUS, True),
    Metrique("rendement_rachats_nets", "Rachats nets d'émissions, rapportés à la capitalisation",
             "dilution", "pourcentage"),
    # Sans objet pour une banque : sa trésorerie est sa matière première, pas une réserve.
    Metrique("autonomie", "Autonomie de trésorerie", "dilution", "trimestres", (STD, REIT),
             note="trésorerie / consommation trimestrielle, pour les sociétés qui brûlent du cash"),

    # --- Risque : cours ------------------------------------------------------------
    Metrique("volatilite_1a", "Volatilité annualisée, 1 an", "risque", "pourcentage"),
    Metrique("drawdown_max_5a", "Pire baisse depuis un sommet, 5 ans", "risque", "pourcentage"),
    Metrique("distance_plus_haut_52s", "Écart au plus haut sur 52 semaines", "risque", "pourcentage"),
]
PAR_ID = {m.id: m for m in METRIQUES}

# Métriques qui exigent cinq ans de cours. Elles ne sont recalculées qu'au passage
# hebdomadaire complet : le passage quotidien ne lit que treize mois de cours et
# reporte leur dernière valeur connue. Relire cinq ans pour tout l'univers chaque soir
# coûtait environ 60 Mo de lecture sur une instance nano, pour une valeur qui bouge
# à peine d'un jour sur l'autre.
HISTORIQUE_LONG = ("drawdown_max_5a",)


# =============================================================================
# OUTILS
# =============================================================================
def _j(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _div(n, d):
    return None if n is None or d is None or d == 0 else n / d


def _cagr(debut, fin, annees):
    if debut is None or fin is None or annees <= 0:
        return None, "donnée manquante"
    if debut <= 0 or fin <= 0:
        return None, "valeur de départ ou d'arrivée négative ou nulle"
    return (fin / debut) ** (1 / annees) - 1, None


def _il_y_a(fy: list, annees: float, tolerance: float = 0.3):
    """L'exercice le plus proche de N années avant le dernier, dans la tolérance."""
    if not fy:
        return None, 0.0
    fin = fy[-1]["period_end"]
    meilleur, ecart_min = None, None
    for r in fy[:-1]:
        a = _j(r["period_end"], fin) / 365.25
        ecart = abs(a - annees)
        if ecart <= tolerance and (ecart_min is None or ecart < ecart_min):
            meilleur, ecart_min = r, ecart
    return (meilleur, _j(meilleur["period_end"], fin) / 365.25) if meilleur else (None, 0.0)


# Libellés des flux, pour que la base de calcul stockée soit lisible telle quelle.
LIBELLES_FLUX = {
    "revenue": "chiffre d'affaires", "net_income": "résultat net",
    "operating_income": "résultat opérationnel", "ebitda": "EBITDA",
    "fcf": "flux libre", "ocf": "flux d'exploitation", "dividends_paid": "dividendes",
    "buybacks": "rachats", "issuance": "émissions", "sbc": "rémunération en actions",
    "interest_expense": "charge d'intérêts",
}


def douze_mois(trimestres: list, exercices: list) -> tuple[dict, str]:
    """
    Somme des quatre derniers trimestres si ce sont bien quatre trimestres consécutifs,
    sinon repli sur le dernier exercice annuel.

    Le Q4 n'est jamais publié : il vient de la reconstruction FY - 9 mois. Un trou dans
    cette reconstruction casserait la consécutivité, d'où le contrôle d'écart entre le
    premier et le dernier des quatre — environ 273 jours pour trois trimestres.
    """
    flux = ("revenue", "net_income", "operating_income", "ebitda", "fcf", "ocf",
            "dividends_paid", "buybacks", "issuance", "sbc", "interest_expense")
    q = sorted(trimestres, key=lambda r: r["period_end"])[-4:]
    fy = exercices[-1] if exercices else {}
    consecutifs = len(q) == 4 and 250 <= _j(q[0]["period_end"], q[3]["period_end"]) <= 300
    somme, replis = {}, []
    for f in flux:
        v = [r.get(f) for r in q] if consecutifs else []
        if v and all(x is not None for x in v):
            somme[f] = sum(v)
        else:
            # Repli CHAMP PAR CHAMP sur le dernier exercice. Dans un 10-Q, le tableau
            # des flux de trésorerie est cumulé depuis le début de l'exercice — 6 mois,
            # 9 mois —, jamais trimestriel : les T2 et T3 n'ont pas de flux isolés, et la
            # somme sur quatre trimestres échouait pour les dividendes, les rachats, le
            # flux libre et les amortissements, donc l'EBITDA. Correction de fond à
            # l'étape 4b, par différence des cumuls à l'extraction.
            somme[f] = fy.get(f)
            if fy.get(f) is not None:
                replis.append(f)
    if not consecutifs:
        return somme, f"exercice clos le {fy.get('period_end', '?')}"
    base = f"quatre trimestres au {q[3]['period_end']}"
    if replis:
        noms = [LIBELLES_FLUX.get(f, f) for f in replis]
        base += f" ; exercice clos le {fy['period_end']} pour {', '.join(noms)}"
    return somme, base


def _dernier_bilan(trimestres: list, exercices: list) -> dict:
    lignes = sorted(trimestres + exercices, key=lambda r: r["period_end"])
    return lignes[-1] if lignes else {}


# =============================================================================
# RATIOS D'UN EXERCICE — servent à la fois à la valeur du jour et à la série annuelle
# =============================================================================
def ratios_exercice(r: dict, profil: str) -> dict:
    """
    {metric_id: (valeur | None, raison | None)} pour un exercice.

    Chaque raison est DÉDUITE de l'état des données, jamais supposée. Une première
    version codait les raisons en dur : Realty Income affichait « pas de charge
    d'intérêts » alors qu'elle porte des milliards de dette — la donnée n'était
    simplement pas balisée. Une raison fausse est pire qu'une absence : elle affirme.
    """
    rev, ni, op = r.get("revenue"), r.get("net_income"), r.get("operating_income")
    eq, debt, cash = r.get("equity"), r.get("total_debt"), r.get("cash")
    ebitda, tax, ie = r.get("ebitda"), r.get("income_tax"), r.get("interest_expense")
    out: dict = {}

    def manque(*paires):
        for valeur, libelle in paires:
            if valeur is None:
                return f"{libelle} non déclaré"
        return None

    # marges : même dénominateur, numérateurs différents
    for mid, num, lib in (("marge_brute", r.get("gross_profit"), "marge brute"),
                          ("marge_operationnelle", op, "résultat opérationnel"),
                          ("marge_nette", ni, "résultat net"),
                          ("sbc_ventes", r.get("sbc"), "rémunération en actions")):
        if rev is not None and rev <= 0:
            out[mid] = (None, "pas de chiffre d'affaires")
        else:
            m = manque((rev, "chiffre d'affaires"), (num, lib))
            out[mid] = (None, m) if m else (num / rev, None)

    m = manque((eq, "capitaux propres"), (ni, "résultat net"))
    out["roe"] = (None, m) if m else ((None, "capitaux propres négatifs") if eq <= 0 else (ni / eq, None))

    m = manque((op, "résultat opérationnel"), (eq, "capitaux propres"), (debt, "endettement"), (cash, "trésorerie"))
    if m:
        out["roic"] = (None, m)
    else:
        capital = eq + debt - cash
        avant_impot = (ni + tax) if (ni is not None and tax is not None) else None
        taux = tax / avant_impot if (tax is not None and avant_impot and avant_impot > 0) else 0.21
        taux = min(max(taux, 0.0), 0.5)
        out["roic"] = (None, "capitaux investis négatifs") if capital <= 0 else (op * (1 - taux) / capital, None)

    if ebitda is None:
        out["dette_nette_ebitda"] = (None, "EBITDA non calculable : résultat opérationnel ou amortissements non déclarés")
    elif ebitda <= 0:
        out["dette_nette_ebitda"] = (None, "EBITDA négatif")
    else:
        m = manque((debt, "endettement"), (cash, "trésorerie"))
        out["dette_nette_ebitda"] = (None, m) if m else ((debt - cash) / ebitda, None)

    if debt == 0:
        out["couverture_interets"] = (None, "aucune dette")
    elif ie is None:
        out["couverture_interets"] = (None, "charge d'intérêts non déclarée")
    elif ie <= 0:
        out["couverture_interets"] = (None, "pas de charge d'intérêts")
    else:
        m = manque((op, "résultat opérationnel"))
        out["couverture_interets"] = (None, m) if m else (op / ie, None)

    ca, cl = r.get("current_assets"), r.get("current_liabilities")
    m = manque((ca, "actif courant"), (cl, "passif courant"))
    out["ratio_liquidite"] = (None, m) if m else ((None, "passif courant nul") if cl == 0 else (ca / cl, None))

    return {k: v for k, v in out.items() if k in PAR_ID and profil in PAR_ID[k].profils}


# =============================================================================
# CALCUL COMPLET
# =============================================================================
def calculer(exercices: list, trimestres: list, cours: list, capitalisation: Optional[float],
             profil: str) -> dict:
    """
    Renvoie {metric_id: (valeur | None, raison | None)}.

    `exercices` et `trimestres` : lignes de `fundamentals`, en dictionnaires.
    `cours` : [(date 'AAAA-MM-JJ', clôture)] croissant.
    """
    fy = sorted(exercices, key=lambda r: r["period_end"])
    res: dict = {}

    def pose(mid, val, raison=None):
        m = PAR_ID[mid]
        if profil not in m.profils:
            res[mid] = (None, f"sans objet pour le profil {profil}")
        elif val is None:
            res[mid] = (None, raison or "donnée manquante")
        else:
            res[mid] = (val, None)

    ttm, source_ttm = douze_mois(trimestres, fy)
    bilan = _dernier_bilan(trimestres, fy)
    cap = capitalisation if capitalisation and capitalisation > 0 else None
    ni, rev = ttm.get("net_income"), ttm.get("revenue")

    # --- valorisation ------------------------------------------------------------
    if cap is None:
        for mid in ("per", "prix_ventes", "prix_valeur_comptable", "ve_ebitda", "ve_fcf",
                    "rendement_dividende", "rendement_rachats_nets"):
            pose(mid, None, "capitalisation inconnue")
    else:
        pose("per", _div(cap, ni) if ni and ni > 0 else None,
             "résultat négatif sur 12 mois" if ni is not None and ni <= 0 else None)
        pose("prix_ventes", _div(cap, rev) if rev and rev > 0 else None,
             "pas de chiffre d'affaires" if rev is not None and rev <= 0 else None)
        eq = bilan.get("equity")
        pose("prix_valeur_comptable", _div(cap, eq) if eq and eq > 0 else None,
             "capitaux propres négatifs" if eq is not None and eq <= 0 else None)
        debt, cash = bilan.get("total_debt"), bilan.get("cash")
        ve = cap + debt - cash if debt is not None and cash is not None else None
        eb, fcf = ttm.get("ebitda"), ttm.get("fcf")
        pose("ve_ebitda", _div(ve, eb) if ve and eb and eb > 0 else None,
             "EBITDA négatif" if eb is not None and eb <= 0 else None)
        pose("ve_fcf", _div(ve, fcf) if ve and fcf and fcf > 0 else None,
             "flux de trésorerie libre négatif" if fcf is not None and fcf <= 0 else None)
        div = ttm.get("dividends_paid")
        pose("rendement_dividende", _div(div, cap) if div is not None else None)
        rach, emis = ttm.get("buybacks"), ttm.get("issuance")
        pose("rendement_rachats_nets",
             _div((rach or 0) - (emis or 0), cap) if rach is not None or emis is not None else None)

    div = ttm.get("dividends_paid")
    pose("taux_distribution", _div(div, ni) if ni and ni > 0 and div is not None else None,
         "résultat négatif sur 12 mois" if ni is not None and ni <= 0 else None)

    # --- croissance : totaux ------------------------------------------------------
    for champ, prefixe, horizons in (("revenue", "cagr_ca", (3, 5)),
                                     ("net_income", "cagr_rn", (3, 5)),
                                     ("fcf", "cagr_fcf", (5,))):
        for n in horizons:
            debut, annees = _il_y_a(fy, n)
            if debut is None:
                pose(f"{prefixe}_{n}a", None, f"moins de {n} exercices disponibles")
            else:
                v, raison = _cagr(debut.get(champ), fy[-1].get(champ), annees)
                pose(f"{prefixe}_{n}a", v, raison)

    cr_rn5 = res.get("cagr_rn_5a", (None,))[0]
    per = res.get("per", (None,))[0]
    pose("peg_historique", per / (cr_rn5 * 100) if per and cr_rn5 and cr_rn5 > 0 else None,
         "croissance passée du résultat nulle ou négative" if cr_rn5 is not None and cr_rn5 <= 0
         else ("PER non applicable" if per is None else None))

    qs = sorted(trimestres, key=lambda r: r["period_end"])
    if qs:
        dernier = qs[-1]
        un_an = [r for r in qs if 345 <= _j(r["period_end"], dernier["period_end"]) <= 385]
        v, raison = (None, "trimestre de comparaison absent")
        if un_an:
            a, b = un_an[-1].get("revenue"), dernier.get("revenue")
            v = _div(b, a) - 1 if a and a > 0 and b is not None else None
            raison = None if v is not None else "chiffre d'affaires absent ou nul"
        pose("croissance_trimestre", v, raison)
    else:
        pose("croissance_trimestre", None, "aucun trimestre déposé")

    # --- dilution -----------------------------------------------------------------
    # Étape 4a : les nombres d'actions EDGAR ne sont pas encore ramenés sur une base
    # commune après une division. Un rapport annuel supérieur à 1,8 ou inférieur à 0,55
    # est donc suspendu : division, regroupement, introduction en bourse ou émission
    # massive — impossible à distinguer sans l'historique des divisions (étape 4b).
    for n in (1, 3, 5):
        debut, annees = _il_y_a(fy, n, tolerance=0.3 if n > 1 else 0.2)
        if debut is None:
            pose(f"cagr_actions_{n}a", None, f"moins de {n} exercice{'s' if n > 1 else ''} disponible{'s' if n > 1 else ''}")
            continue
        fenetre = [r for r in fy if r["period_end"] >= debut["period_end"]]
        actions = [r.get("shares_diluted") for r in fenetre]
        if any(a is None or a <= 0 for a in actions):
            pose(f"cagr_actions_{n}a", None, "nombre d'actions manquant sur la période")
            continue
        sauts = [b / a for a, b in zip(actions, actions[1:])]
        if any(s > 1.8 or s < 0.55 for s in sauts):
            pose(f"cagr_actions_{n}a", None,
                 "variation brutale du nombre d'actions : division, regroupement, introduction "
                 "ou émission massive, à confirmer avec l'historique des divisions")
            continue
        v, raison = _cagr(actions[0], actions[-1], annees)
        pose(f"cagr_actions_{n}a", v, raison)

    fcf = ttm.get("fcf")
    tres = bilan.get("cash")
    if fcf is not None and fcf < 0 and tres is not None:
        pose("autonomie", tres / (-fcf / 4))
    else:
        pose("autonomie", None, "génère du cash" if fcf is not None and fcf >= 0 else None)

    # --- qualité : dernier exercice -----------------------------------------------
    if fy:
        dernier_ratios = ratios_exercice(fy[-1], profil)
        for m in METRIQUES:
            if m.axe == "qualite" or m.id == "sbc_ventes":
                v, raison = dernier_ratios.get(m.id, (None, None))
                pose(m.id, v, raison)

    # --- risque : cours -----------------------------------------------------------
    closes = [c for _, c in cours if c and c > 0]
    if len(closes) >= 200:
        an = closes[-252:]
        rend = [math.log(b / a) for a, b in zip(an, an[1:])]
        pose("volatilite_1a", pstdev(rend) * math.sqrt(252))
        pose("distance_plus_haut_52s", an[-1] / max(an) - 1)
        pic, pire = closes[-1260:][0], 0.0
        for c in closes[-1260:]:
            pic = max(pic, c)
            pire = min(pire, c / pic - 1)
        pose("drawdown_max_5a", pire)
    else:
        for mid in ("volatilite_1a", "distance_plus_haut_52s", "drawdown_max_5a"):
            pose(mid, None, "moins d'un an de cours")

    res["_base_12_mois"] = (None, source_ttm)
    return res


def serie_annuelle(exercices: list, profil: str, n: int = 6) -> dict:
    """{metric_id: [(period_end, valeur)]} sur les n derniers exercices, pour les ratios."""
    fy = sorted(exercices, key=lambda r: r["period_end"])[-n:]
    out: dict = {}
    for r in fy:
        for mid, (v, _) in ratios_exercice(r, profil).items():
            if PAR_ID[mid].serie_annuelle and v is not None:
                out.setdefault(mid, []).append((r["period_end"], v))
    return out


# =============================================================================
# EXPORT pour l'interface — python metriques.py > ../web/lib/metriques.json
# =============================================================================
AXES = [
    ("valorisation", "Valorisation"),
    ("qualite", "Qualité et solidité"),
    ("croissance", "Croissance annuelle moyenne"),
    ("dilution", "Dilution"),
    ("risque", "Risque"),
]

if __name__ == "__main__":
    import json
    print(json.dumps({
        "axes": [{"id": a, "libelle": l} for a, l in AXES],
        "metriques": [
            {"id": m.id, "libelle": m.libelle, "axe": m.axe, "format": m.format,
             "serie": m.serie_annuelle, "perte": m.perte, "note": m.note}
            for m in METRIQUES
        ],
    }, ensure_ascii=False, indent=1))
