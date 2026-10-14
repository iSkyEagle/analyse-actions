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
from datetime import date, timedelta
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
    # Hors foncières : leur résultat net est écrasé par l'amortissement des immeubles, et
    # le taux ressortait au-dessus de 100 % pour presque toutes — 230 % chez Realty Income.
    Metrique("taux_distribution", "Taux de distribution", "valorisation", "pourcentage", (STD, FIN)),
    # Foncières : la convention du secteur. Une métrique distincte plutôt qu'une
    # définition qui changerait selon le profil : un libellé, une formule.
    Metrique("taux_distribution_ffo", "Taux de distribution sur FFO", "valorisation", "pourcentage", (REIT,),
             note="dividendes / (résultat net + amortissements) des 12 derniers mois — au-delà de 100 %, "
                  "le dividende n'est plus couvert par le cash généré"),
    # Foncières : l'amortissement de l'immobilier écrase le résultat net sans toucher à
    # la trésorerie. Le PER de Realty Income ressortait à 41 et son taux de distribution
    # à 224 % — deux artefacts. La mesure d'usage est le FFO : résultat net plus
    # amortissements (approximation : les plus-values de cession n'en sont pas retirées).
    Metrique("prix_ffo", "Prix sur FFO", "valorisation", "multiple", (REIT,),
             note="capitalisation / (résultat net + amortissements) des 12 derniers mois"),
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
    # BPA sur base actuelle : résultat net / actions ramenées sur la base des cours.
    # Le BPA publié change d'unité à chaque division ; sa croissance brute n'a pas de sens.
    Metrique("cagr_bpa_3a", "Bénéfice par action, 3 ans", "croissance", "pourcentage"),
    Metrique("cagr_bpa_5a", "Bénéfice par action, 5 ans", "croissance", "pourcentage"),
    Metrique("cagr_fcf_5a", "Flux de trésorerie libre, 5 ans", "croissance", "pourcentage", (STD, REIT)),
    Metrique("croissance_trimestre", "Dernier trimestre sur un an", "croissance", "pourcentage"),
    # Rendement total : cours ajusté des divisions et dividendes réinvestis, face à
    # ^SP500TR, l'indice dividendes réinvestis. Un cours nu face à un indice qui
    # incorpore ses dividendes désavantagerait toute valeur de rendement.
    Metrique("rendement_total_1a", "Rendement total, 1 an", "croissance", "pourcentage"),
    Metrique("ecart_sp500_1a", "Écart au S&P 500, 1 an", "croissance", "points"),
    Metrique("rendement_total_3a", "Rendement total annuel, 3 ans", "croissance", "pourcentage"),
    Metrique("ecart_sp500_3a", "Écart au S&P 500, 3 ans", "croissance", "points"),
    Metrique("rendement_total_5a", "Rendement total annuel, 5 ans", "croissance", "pourcentage"),
    Metrique("ecart_sp500_5a", "Écart au S&P 500, 5 ans", "croissance", "points"),

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
    Metrique("beta_5a", "Bêta, 5 ans", "risque", "multiple",
             note="rendements mensuels sur 5 ans face au S&P 500, dividendes réinvestis — la convention de Yahoo"),
]
PAR_ID = {m.id: m for m in METRIQUES}

# =============================================================================
# EXPLICATIONS — une ligne par métrique, en mots simples (étape 4d)
#
# Ce que la mesure dit, et dans quel sens elle se lit. Jamais de conseil : « plus il
# est bas, moins le marché paie », pas « un PER bas est une opportunité ».
# =============================================================================
EXPLICATIONS = {
    # --- valorisation
    "per": "Prix payé pour un dollar de bénéfice annuel. Plus il est élevé, plus le marché paie cher les bénéfices actuels, souvent parce qu'il attend de la croissance.",
    "prix_ventes": "Prix payé pour un dollar de chiffre d'affaires annuel. Utile quand la société ne fait pas encore de bénéfice.",
    "prix_valeur_comptable": "Prix payé pour un dollar de capitaux propres inscrits au bilan. La mesure de référence pour les banques.",
    "ve_ebitda": "Prix de toute l'entreprise, dette comprise et trésorerie déduite, rapporté à son résultat d'exploitation avant amortissements. Compare des sociétés plus ou moins endettées.",
    "ve_fcf": "Prix de toute l'entreprise, dette comprise, rapporté au cash qu'elle génère après ses investissements.",
    "rendement_dividende": "Dividendes versés sur un an, rapportés au prix de l'action.",
    "taux_distribution": "Part du bénéfice reversée en dividendes. Au-delà de 100 %, la société distribue plus qu'elle ne gagne.",
    "taux_distribution_ffo": "Part du cash généré (le FFO) reversée en dividendes. La mesure d'usage pour une foncière : au-delà de 100 %, le dividende n'est plus couvert.",
    "prix_ffo": "Le PER des foncières : prix rapporté au résultat avant amortissement des immeubles, une charge comptable qui ne sort pas de la caisse.",
    "peg_historique": "PER divisé par la croissance annuelle passée du résultat net. Autour de 1, le prix est en ligne avec la croissance passée ; la croissance future peut être différente.",
    # --- qualité
    "marge_brute": "Ce qui reste du chiffre d'affaires après le coût direct de ce qui est vendu. Mesure la force du produit face à ses coûts.",
    "marge_operationnelle": "Ce qui reste du chiffre d'affaires après toutes les dépenses d'exploitation. Mesure l'efficacité de l'activité elle-même.",
    "marge_nette": "Ce qui reste du chiffre d'affaires une fois tout payé, impôts et intérêts compris.",
    "roe": "Bénéfice rapporté à l'argent des actionnaires. Une valeur élevée peut venir d'une activité rentable, ou d'un fort endettement.",
    "roic": "Rentabilité de tout l'argent investi dans l'activité, celui des actionnaires comme celui des prêteurs.",
    "dette_nette_ebitda": "Nombre d'années de résultat d'exploitation qu'il faudrait pour rembourser la dette, trésorerie déduite. Négatif : la société a plus de cash que de dette.",
    "couverture_interets": "Combien de fois le résultat d'exploitation couvre les intérêts de la dette. Sous 2, la marge de sécurité est mince.",
    "ratio_liquidite": "Ce que la société possède à court terme face à ce qu'elle doit à court terme. Sous 1, elle doit plus qu'elle ne possède sur l'année.",
    # --- croissance
    "cagr_ca_3a": "Croissance annuelle moyenne du chiffre d'affaires sur les trois derniers exercices.",
    "cagr_ca_5a": "Croissance annuelle moyenne du chiffre d'affaires sur les cinq derniers exercices.",
    "cagr_rn_3a": "Croissance annuelle moyenne du résultat net sur trois exercices. Non calculée si le résultat est négatif à l'une des deux dates.",
    "cagr_rn_5a": "Croissance annuelle moyenne du résultat net sur cinq exercices. Non calculée si le résultat est négatif à l'une des deux dates.",
    "cagr_bpa_3a": "Croissance annuelle du bénéfice par action sur trois exercices, corrigée des divisions d'actions. Plus rapide que le résultat net quand la société rachète ses actions.",
    "cagr_bpa_5a": "Croissance annuelle du bénéfice par action sur cinq exercices, corrigée des divisions d'actions.",
    "cagr_fcf_5a": "Croissance annuelle moyenne du cash généré après investissements, sur cinq exercices.",
    "croissance_trimestre": "Chiffre d'affaires du dernier trimestre comparé au même trimestre un an plus tôt.",
    "rendement_total_1a": "Ce qu'aurait rapporté l'action sur un an, dividendes réinvestis.",
    "ecart_sp500_1a": "Rendement total de l'action moins celui du S&P 500 sur un an, dividendes réinvestis des deux côtés.",
    "rendement_total_3a": "Rendement annuel moyen de l'action sur trois ans, dividendes réinvestis.",
    "ecart_sp500_3a": "Écart annuel moyen avec le S&P 500 sur trois ans, dividendes réinvestis des deux côtés.",
    "rendement_total_5a": "Rendement annuel moyen de l'action sur cinq ans, dividendes réinvestis.",
    "ecart_sp500_5a": "Écart annuel moyen avec le S&P 500 sur cinq ans, dividendes réinvestis des deux côtés.",
    # --- dilution
    "cagr_actions_1a": "Évolution du nombre d'actions sur un an. En hausse, chaque action détient une part plus petite de l'entreprise ; en baisse, la société rachète ses actions.",
    "cagr_actions_3a": "Évolution annuelle moyenne du nombre d'actions sur trois ans, corrigée des divisions d'actions.",
    "cagr_actions_5a": "Évolution annuelle moyenne du nombre d'actions sur cinq ans, corrigée des divisions. Un saut brutal signale souvent une introduction en bourse ou une fusion.",
    "sbc_ventes": "Salaires payés en actions, rapportés au chiffre d'affaires. Une dépense réelle, qui dilue les actionnaires au lieu de sortir de la caisse.",
    "rendement_rachats_nets": "Rachats d'actions moins émissions sur un an, rapportés au prix de la société. Positif : la société rend du cash à ses actionnaires.",
    "autonomie": "Pour une société qui consomme du cash : combien de trimestres sa trésorerie peut tenir au rythme actuel, avant de devoir lever des fonds.",
    # --- risque
    "volatilite_1a": "Ampleur habituelle des variations du cours sur un an, ramenée à l'année. Le S&P 500 tourne autour de 15 à 20 %.",
    "drawdown_max_5a": "Plus forte chute du cours depuis un sommet, sur cinq ans : ce qu'aurait perdu, au pire moment, qui avait acheté au plus haut.",
    "distance_plus_haut_52s": "Écart entre le cours actuel et son plus haut de l'année écoulée.",
    "beta_5a": "Sensibilité de l'action aux mouvements du marché. À 1, elle bouge comme le S&P 500 ; à 2, deux fois plus ; sous 1, moins.",
}


# Métriques qui exigent cinq ans de cours. Elles ne sont recalculées qu'au passage
# hebdomadaire complet : le passage quotidien ne lit que treize mois de cours et
# reporte leur dernière valeur connue. Relire cinq ans pour tout l'univers chaque soir
# coûtait environ 60 Mo de lecture sur une instance nano, pour une valeur qui bouge
# à peine d'un jour sur l'autre.
HISTORIQUE_LONG = ("drawdown_max_5a", "rendement_total_3a", "ecart_sp500_3a",
                   "rendement_total_5a", "ecart_sp500_5a", "beta_5a")


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
    "interest_expense": "charge d'intérêts", "d_and_a": "amortissements",
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
            "dividends_paid", "buybacks", "issuance", "sbc", "interest_expense", "d_and_a")
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
             profil: str, divisions: Optional[list] = None,
             dividendes: Optional[list] = None, indice: Optional[list] = None) -> dict:
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

    da = ttm.get("d_and_a")
    ffo = ni + da if ni is not None and da is not None else None
    pose("prix_ffo", _div(cap, ffo) if cap and ffo and ffo > 0 else None,
         "FFO négatif" if ffo is not None and ffo <= 0 else
         ("amortissements non déclarés" if da is None else None))

    div = ttm.get("dividends_paid")
    pose("taux_distribution_ffo", _div(div, ffo) if ffo and ffo > 0 and div is not None else None,
         "FFO négatif" if ffo is not None and ffo <= 0 else
         ("amortissements non déclarés" if da is None else None))
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
    # Nombres d'actions ramenés sur la base actuelle par les divisions connues (4c). La
    # suspension au-delà de x1,8 n'est conservée QUE sans historique des divisions : une
    # variation qui subsiste après correction est un vrai événement sur le capital —
    # introduction, fusion, émission massive —, que la règle 4 du moteur d'analyse
    # signalera, et qui doit apparaître dans le chiffre.
    def actions_de(r):
        return actions_base_actuelle(r, divisions) if divisions is not None else r.get("shares_diluted")

    for n in (1, 3, 5):
        debut, annees = _il_y_a(fy, n, tolerance=0.3 if n > 1 else 0.2)
        if debut is None:
            pose(f"cagr_actions_{n}a", None, f"moins de {n} exercice{'s' if n > 1 else ''} disponible{'s' if n > 1 else ''}")
            continue
        fenetre = [r for r in fy if r["period_end"] >= debut["period_end"]]
        actions = [actions_de(r) for r in fenetre]
        if any(x is None or x <= 0 for x in actions):
            pose(f"cagr_actions_{n}a", None, "nombre d'actions manquant sur la période")
            continue
        if divisions is None:
            sauts = [y / x for x, y in zip(actions, actions[1:])]
            if any(s > 1.8 or s < 0.55 for s in sauts):
                pose(f"cagr_actions_{n}a", None,
                     "variation brutale du nombre d'actions : division, regroupement, introduction "
                     "ou émission massive, à confirmer avec l'historique des divisions")
                continue
        v, raison = _cagr(actions[0], actions[-1], annees)
        pose(f"cagr_actions_{n}a", v, raison)

    # --- BPA sur base actuelle --------------------------------------------------------
    for n in (3, 5):
        debut, annees = _il_y_a(fy, n)
        if debut is None:
            pose(f"cagr_bpa_{n}a", None, f"moins de {n} exercices disponibles")
            continue
        a0, a1 = actions_de(debut), actions_de(fy[-1])
        r0, r1 = debut.get("net_income"), fy[-1].get("net_income")
        if not a0 or not a1 or r0 is None or r1 is None:
            pose(f"cagr_bpa_{n}a", None, "résultat net ou nombre d'actions manquant")
            continue
        v, raison = _cagr(r0 / a0, r1 / a1, annees)
        pose(f"cagr_bpa_{n}a", v, raison)

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
    for mid, (v, raison) in risque_cours(cours).items():
        pose(mid, v, raison)

    # --- rendement total, écart au S&P 500, bêta -----------------------------------
    if indice:
        rt_soc = serie_rendement_total(cours, dividendes or [])
        for n in (1, 3, 5):
            r_soc = performance_annualisee(rt_soc, n)
            r_ind = performance_annualisee(indice, n)
            raison = f"moins de {n} an{'s' if n > 1 else ''} de cours"
            pose(f"rendement_total_{n}a", r_soc, raison)
            pose(f"ecart_sp500_{n}a", r_soc - r_ind if r_soc is not None and r_ind is not None else None, raison)
        pose("beta_5a", beta_mensuel(rt_soc, indice), "moins de 36 mois de cours")
    else:
        for mid in ("rendement_total_1a", "ecart_sp500_1a", "rendement_total_3a", "ecart_sp500_3a",
                    "rendement_total_5a", "ecart_sp500_5a", "beta_5a"):
            pose(mid, None, "indice de référence indisponible")

    res["_base_12_mois"] = (None, source_ttm)
    return res


def risque_cours(cours: list) -> dict:
    """Volatilité, écart au plus haut et pire baisse, sur les clôtures ajustées."""
    closes = [c for _, c in cours if c and c > 0]
    if len(closes) < 200:
        return {mid: (None, "moins d'un an de cours")
                for mid in ("volatilite_1a", "distance_plus_haut_52s", "drawdown_max_5a")}
    an = closes[-252:]
    rend = [math.log(b / a) for a, b in zip(an, an[1:])]
    pic, pire = closes[-1260:][0], 0.0
    for c in closes[-1260:]:
        pic = max(pic, c)
        pire = min(pire, c / pic - 1)
    return {"volatilite_1a": (pstdev(rend) * math.sqrt(252), None),
            "distance_plus_haut_52s": (an[-1] / max(an) - 1, None),
            "drawdown_max_5a": (pire, None)}


# Mesures de cours du S&P 500, pour la comparaison d'une valeur à l'indice (étape 5).
# Les mêmes formules que pour une société : le risque sur l'indice de prix (^GSPC), comme
# le cours d'une société ajusté des seules divisions ; le rendement total sur l'indice
# dividendes réinvestis (^SP500TR), comme celui d'une société. Le bêta de l'indice face à
# lui-même vaut 1 par définition.
MESURES_INDICE = ("volatilite_1a", "distance_plus_haut_52s", "drawdown_max_5a",
                  "rendement_total_1a", "rendement_total_3a", "rendement_total_5a", "beta_5a")


def mesures_indice(cours_prix: list, cours_rt: list) -> dict:
    res = risque_cours(cours_prix)
    for n in (1, 3, 5):
        v = performance_annualisee(cours_rt, n) if cours_rt else None
        res[f"rendement_total_{n}a"] = (v, None if v is not None
                                        else f"moins de {n} an{'s' if n > 1 else ''} de cours")
    res["beta_5a"] = (1.0, None)
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
# ÉTAPE 4c — MULTIPLES POINT-IN-TIME
#
# Pour chaque séance, les multiples sont calculés avec les SEULS comptes publiés à
# cette date : une ligne n'est connue qu'à partir de sa première publication
# (`first_filed`). Les nombres d'actions sont ramenés sur la base actuelle, celle des
# cours ajustés des divisions. La série n'est jamais stockée : seul son résumé l'est.
# =============================================================================
MULTIPLES_HISTORIQUES = ("per", "prix_ventes", "prix_valeur_comptable", "ve_ebitda",
                         "ve_fcf", "prix_ffo", "rendement_dividende")


def facteur_division(depuis: str, divisions: list) -> float:
    """
    Produit des ratios des divisions postérieures à `depuis`.

    Un nombre d'actions publié avant une division est en unités d'avant : NVIDIA
    déclarait 2 535 M d'actions pour 2022 dans un dépôt de février 2024, avant sa
    division par dix de juin 2024. Les cours, eux, sont ajustés de toutes les
    divisions jusqu'à aujourd'hui : il faut ramener les actions sur la même base.
    """
    f = 1.0
    for d, ratio in divisions:
        if d > depuis:
            f *= ratio
    return f


def actions_base_actuelle(ligne: dict, divisions: list) -> Optional[float]:
    s = ligne.get("shares_diluted")
    if s is None or s <= 0:
        return None
    source = ligne.get("shares_filed") or ligne.get("filed")
    return s * facteur_division(source, divisions) if source else s


def _etats_connus(trimestres: list, exercices: list, divisions: list) -> list:
    """
    [(date, état)] croissant : l'état des comptes tel qu'un lecteur pouvait le
    connaître à cette date — flux sur douze mois, dernier bilan, nombre d'actions.
    Une entrée par date de publication ; entre deux, l'état ne change pas.
    """
    cle = lambda r: r["period_end"]
    q_tous = sorted((r for r in trimestres if r.get("first_filed")), key=cle)
    fy_tous = sorted((r for r in exercices if r.get("first_filed")), key=cle)
    etats = []
    for d in sorted({r["first_filed"] for r in q_tous + fy_tous}):
        q = [r for r in q_tous if r["first_filed"] <= d]
        fy = [r for r in fy_tous if r["first_filed"] <= d]
        ttm, _ = douze_mois(q, fy)
        avec_actions = [r for r in q + fy if r.get("shares_diluted")]
        actions = (actions_base_actuelle(max(avec_actions, key=cle), divisions)
                   if avec_actions else None)
        etats.append((d, {"ttm": ttm, "bilan": _dernier_bilan(q, fy), "actions": actions}))
    return etats


def _multiples_du_jour(cours: float, etat: dict, profil: str) -> dict:
    """Mêmes formules que la valeur du jour de calculer(), sur un état passé."""
    actions = etat["actions"]
    if not actions or not cours:
        return {}
    cap = cours * actions
    t, b = etat["ttm"], etat["bilan"]
    ni, rev, eb, fcf = t.get("net_income"), t.get("revenue"), t.get("ebitda"), t.get("fcf")
    eq, debt, cash = b.get("equity"), b.get("total_debt"), b.get("cash")
    ve = cap + debt - cash if debt is not None and cash is not None else None
    da = t.get("d_and_a")
    ffo = ni + da if ni is not None and da is not None else None
    v = {
        "per": cap / ni if ni and ni > 0 else None,
        "prix_ventes": cap / rev if rev and rev > 0 else None,
        "prix_valeur_comptable": cap / eq if eq and eq > 0 else None,
        "ve_ebitda": ve / eb if ve and eb and eb > 0 else None,
        "ve_fcf": ve / fcf if ve and fcf and fcf > 0 else None,
        "prix_ffo": cap / ffo if ffo and ffo > 0 else None,
        "rendement_dividende": (t.get("dividends_paid") / cap
                                if t.get("dividends_paid") is not None else None),
    }
    return {k: x for k, x in v.items() if profil in PAR_ID[k].profils}


def multiples_historiques(trimestres: list, exercices: list, jours: list,
                          divisions: list, profil: str) -> dict:
    """
    {multiple: [valeur | None par séance]}, alignées sur `jours` [(date, clôture)].
    None : non applicable ce jour-là (résultat négatif…) ou comptes pas encore publiés.
    """
    etats = _etats_connus(trimestres, exercices, divisions)
    series = {m: [] for m in MULTIPLES_HISTORIQUES if profil in PAR_ID[m].profils}
    i = -1
    for d, cours in jours:
        while i + 1 < len(etats) and etats[i + 1][0] <= d:
            i += 1
        v = _multiples_du_jour(cours, etats[i][1], profil) if i >= 0 else {}
        for m in series:
            series[m].append(v.get(m))
    return series


def resume_historique(serie: list, actuelle: Optional[float]) -> Optional[dict]:
    """
    Résumé d'une série sur 5 ans : étendue, 10e et 90e centiles, médiane, et rang de la
    valeur du jour dans cette distribution. `couverture` : part des séances où le
    multiple était défini. Sous 50 %, la règle historique n'est pas affichée — une
    médiane calculée sur une minorité de séances ne représente pas la société.
    """
    valides = sorted(v for v in serie if v is not None and math.isfinite(v))
    if not serie:
        return None
    res = {"n": len(valides), "couverture": len(valides) / len(serie)}
    if len(valides) < 60:
        return res
    q = lambda p: valides[round(p * (len(valides) - 1))]
    res.update(p10=q(0.10), mediane=q(0.50), p90=q(0.90), mini=valides[0], maxi=valides[-1])
    if actuelle is not None:
        from bisect import bisect_left
        res["rang"] = bisect_left(valides, actuelle) / len(valides)
    return res


# =============================================================================
# ÉTAPE 4c — RENDEMENT TOTAL ET BÊTA
# =============================================================================
def serie_rendement_total(jours: list, dividendes: list) -> list:
    """
    [(date, valeur)] : un dollar investi à la première séance, dividendes réinvestis.

    Le jour du détachement, la clôture a déjà baissé du montant du dividende : le
    rajouter à cette clôture donne le rendement total de la séance. Les montants de
    yfinance sont ajustés des divisions, comme les cours.
    """
    div = {}
    for d, x in dividendes:
        div[d] = div.get(d, 0.0) + x
    out, valeur, prec = [], 1.0, None
    for d, c in jours:
        if prec:
            valeur *= (c + div.get(d, 0.0)) / prec
        out.append((d, valeur))
        prec = c
    return out


def performance_annualisee(serie: list, annees: int) -> Optional[float]:
    """Rendement annualisé sur les N dernières années ; None si l'historique est trop court."""
    if not serie:
        return None
    fin = date.fromisoformat(serie[-1][0])
    cible = date(fin.year - annees, fin.month, min(fin.day, 28)).isoformat()
    # Une semaine de tolérance : la séance de départ peut tomber un jour férié.
    if serie[0][0] > (date.fromisoformat(cible) + timedelta(days=7)).isoformat():
        return None
    depart = next((v for d, v in reversed(serie) if d <= cible), serie[0][1])
    if depart <= 0:
        return None
    return (serie[-1][1] / depart) ** (1 / annees) - 1


def _fins_de_mois(serie: list) -> dict:
    """{'AAAA-MM': dernière valeur du mois}."""
    out = {}
    for d, v in serie:
        out[d[:7]] = v
    return out


def beta_mensuel(serie: list, indice: list, mois: int = 60, minimum: int = 36) -> Optional[float]:
    """
    Bêta : covariance des rendements mensuels de la valeur et de l'indice, rapportée à
    la variance de l'indice. Le mois en cours, incomplet, est exclu.
    """
    a, b = _fins_de_mois(serie), _fins_de_mois(indice)
    communs = sorted(set(a) & set(b))
    if communs and communs[-1] == date.today().isoformat()[:7]:
        communs = communs[:-1]
    communs = communs[-(mois + 1):]
    rs = [a[y] / a[x] - 1 for x, y in zip(communs, communs[1:]) if a[x] > 0]
    ri = [b[y] / b[x] - 1 for x, y in zip(communs, communs[1:]) if b[x] > 0]
    if len(rs) < minimum or len(rs) != len(ri):
        return None
    ms, mi = sum(rs) / len(rs), sum(ri) / len(ri)
    cov = sum((x - ms) * (y - mi) for x, y in zip(rs, ri))
    var = sum((y - mi) ** 2 for y in ri)
    return cov / var if var > 0 else None

# =============================================================================
# ÉTAPE 4d — VERDICT DE VALORISATION FACE À L'HISTORIQUE
#
# Situe le multiple de référence du jour dans sa propre distribution sur 5 ans.
# Indépendant du secteur par construction : la société est comparée à elle-même.
# La comparaison au secteur viendra en phase 3, comme une seconde affirmation.
#
# Le verdict est DESCRIPTIF : « moins chère que d'habitude », jamais « peu chère ».
# Recalculé chaque soir avec les cours ; la phrase elle-même est composée par
# l'interface, à partir du code de palier, du multiple et de sa médiane.
# =============================================================================
# Multiple de référence par profil, avec repli si le premier n'est pas applicable
# (société en perte : pas de PER) ou n'a pas d'historique suffisant.
REFERENCE_VALORISATION = {
    "standard": ("per", "prix_ventes"),
    "financial": ("prix_valeur_comptable", "per"),
    "reit": ("prix_ffo", "prix_ventes"),
}

# Paliers, du plus bas au plus haut. La position z vaut 0 à la médiane, -1 au 10e
# centile, +1 au 90e : elle s'adapte à la dispersion propre à chaque société — un écart
# de 15 % est énorme pour Coca-Cola, anodin pour NVIDIA.
PALIERS = ("parmi_plus_bas", "moins_cher", "moyenne", "plus_cher", "parmi_plus_hauts")
_SEUILS = (-1.0, -0.5, 0.5, 1.0)
# Pour changer de palier, il faut franchir le seuil de cette marge : une action qui
# oscille autour d'une frontière ne doit pas changer de verdict un jour sur deux.
MARGE_PALIER = 0.15
VERDICT_ID = "_verdict_valorisation"


def position_historique(valeur: float, resume: dict) -> Optional[float]:
    """z : 0 à la médiane, -1 au 10e centile, +1 au 90e. Au-delà, plus loin que ±1."""
    med, p10, p90 = resume.get("mediane"), resume.get("p10"), resume.get("p90")
    if med is None or p10 is None or p90 is None or valeur is None:
        return None
    if valeur >= med:
        return (valeur - med) / (p90 - med) if p90 > med else 0.0
    return -(med - valeur) / (med - p10) if med > p10 else 0.0


def _palier(z: float) -> int:
    return sum(1 for s in _SEUILS if z > s) if z > _SEUILS[0] else 0


def palier_stable(z: float, precedent: Optional[int]) -> int:
    """Palier de z, avec une marge à franchir pour quitter le palier précédent."""
    n = _palier(z)
    if precedent is None or n == precedent:
        return n
    return _palier(z - MARGE_PALIER) if n > precedent else _palier(z + MARGE_PALIER)


def verdict_valorisation(res: dict, resumes: dict, profil: str,
                         precedent: Optional[str] = None) -> tuple:
    """
    (z | None, 'palier|multiple' | raison) — la forme d'une ligne de `metrics`.

    `res` : métriques du jour ; `resumes` : {multiple: résumé sur 5 ans} ;
    `precedent` : raison stockée la veille, pour la marge entre paliers. La marge ne
    s'applique que si le multiple de référence n'a pas changé.
    """
    prec_palier, prec_multiple = None, None
    if precedent and "|" in precedent:
        code, prec_multiple = precedent.split("|", 1)
        prec_palier = PALIERS.index(code) if code in PALIERS else None
    for mid in REFERENCE_VALORISATION.get(profil, ()):
        valeur = res.get(mid, (None,))[0]
        r = resumes.get(mid)
        if valeur is None or not r or (r.get("couverture") or 0) < 0.5 or r.get("mediane") is None:
            continue
        z = position_historique(valeur, r)
        if z is None:
            continue
        p = palier_stable(z, prec_palier if prec_multiple == mid else None)
        return (z, f"{PALIERS[p]}|{mid}")
    return (None, "historique de valorisation insuffisant")


# =============================================================================
# ÉTAPE 3b — PERCENTILES SECTORIELS
#
# Règle 1 du mémoire : une métrique relative ne se juge que face à son secteur. Une
# marge brute de 25 % est mauvaise pour un éditeur de logiciels, bonne pour un
# distributeur. Ce module situe chaque valeur dans son groupe ; il ne la juge pas — la
# notation viendra en 3c.
# =============================================================================
MIN_GROUPE = 15     # sous 15 sociétés, être premier ne prouve rien


def _position(tries: list, v: float) -> float:
    """Part des valeurs sous v, égalités pour moitié, en pour cent."""
    from bisect import bisect_left, bisect_right
    bas, haut = bisect_left(tries, v), bisect_right(tries, v)
    return 100.0 * (bas + 0.5 * (haut - bas)) / len(tries)


def _quantile(tries: list, p: float) -> float:
    return tries[round(p * (len(tries) - 1))]


def percentiles(lignes: list) -> tuple[list, list]:
    """
    `lignes` : (company_id, metric_id, valeur, secteur, industrie, dans_univers).

    Les groupes de référence ne contiennent que les sociétés de l'univers ; toute société
    classée reçoit sa position, y compris hors univers — une petite société se compare
    ainsi aux sociétés suivies. Les valeurs non applicables sont absentes en amont : elles
    ne comptent jamais comme des zéros (règle 2).

    Renvoie :
      positions — (company_id, metric_id, percentile 0-100, niveau, effectif)
      stats     — (niveau, groupe, metric_id, n, p10, p25, médiane, p75, p90)
    """
    groupes: dict = {}
    for cid, mid, v, sect, ind, univ in lignes:
        if not univ or v is None or not math.isfinite(v) or not sect or sect == "Non classé":
            continue
        groupes.setdefault(("industrie", ind, mid), []).append(v)
        groupes.setdefault(("secteur", sect, mid), []).append(v)
    for k in groupes:
        groupes[k].sort()

    positions = []
    for cid, mid, v, sect, ind, univ in lignes:
        if v is None or not math.isfinite(v) or not sect or sect == "Non classé":
            continue
        for niveau, nom in (("industrie", ind), ("secteur", sect)):
            ref = groupes.get((niveau, nom, mid))
            if ref and len(ref) >= MIN_GROUPE:
                positions.append((cid, mid, round(_position(ref, v)), niveau, len(ref)))
                break

    stats = [(niveau, nom, mid, len(ref), _quantile(ref, .10), _quantile(ref, .25),
              _quantile(ref, .50), _quantile(ref, .75), _quantile(ref, .90))
             for (niveau, nom, mid), ref in groupes.items() if len(ref) >= MIN_GROUPE]
    return positions, stats


# =============================================================================
# ÉTAPE 3c — NOTES PAR AXE ET NOTE GLOBALE
#
# Règles 1 à 3 du mémoire. Chaque métrique est notée de 0 à 10 d'après sa position dans
# son groupe de comparaison, dans le sens qui convient : une marge élevée note haut, un
# PER élevé note bas. La note d'un axe est la moyenne pondérée des métriques notables.
# Les poids sont des hypothèses assumées, validées le 13 octobre 2026.
# =============================================================================
HAUT, BAS = "haut", "bas"       # sens dans lequel la métrique est favorable

# Pseudo-métriques de l'axe valorisation, résolues selon le profil.
REF_VALO = "_reference"         # multiple de référence : PER, valeur comptable, FFO
HISTO_VALO = "_historique"      # position du multiple de référence face à son historique

NOTATION = {
    "valorisation": [
        (REF_VALO, 2.0, BAS), (HISTO_VALO, 1.0, BAS), ("prix_ventes", 1.0, BAS),
        ("ve_ebitda", 1.0, BAS), ("ve_fcf", 1.0, BAS),
    ],
    "qualite": [
        ("roic", 1.5, HAUT), ("marge_operationnelle", 1.0, HAUT), ("marge_nette", 1.0, HAUT),
        ("roe", 1.0, HAUT), ("dette_nette_ebitda", 1.0, BAS), ("couverture_interets", 1.0, HAUT),
        ("marge_brute", 0.5, HAUT),
    ],
    "croissance": [
        ("cagr_ca_3a", 1.0, HAUT), ("cagr_ca_5a", 1.0, HAUT), ("cagr_bpa_3a", 1.0, HAUT),
        ("cagr_bpa_5a", 1.0, HAUT), ("cagr_fcf_5a", 1.0, HAUT),
        ("croissance_trimestre", 0.5, HAUT), ("ecart_sp500_3a", 0.5, HAUT),
    ],
    # Le mémoire réunit dilution et risque en un seul axe ; le risque y pèse peu.
    "dilution": [
        ("cagr_actions_3a", 1.5, BAS), ("cagr_actions_5a", 1.0, BAS), ("cagr_actions_1a", 0.5, BAS),
        ("sbc_ventes", 1.0, BAS), ("rendement_rachats_nets", 1.0, HAUT), ("autonomie", 1.0, HAUT),
        ("volatilite_1a", 0.5, BAS), ("drawdown_max_5a", 0.5, HAUT),
    ],
}
AXES_NOTES = ("valorisation", "qualite", "croissance", "dilution")

# Seuils absolus du mémoire (règle 1) : franchis, ils plafonnent la note de la métrique,
# quel que soit son rang. Être le moins endetté d'un secteur très endetté ne rend pas solide.
PLAFOND_ABSOLU = 2.0
SEUILS_ABSOLUS = {
    "dette_nette_ebitda": lambda v: v > 4,
    "couverture_interets": lambda v: v < 2,
    "cagr_actions_1a": lambda v: v > 0.10,
    "cagr_actions_3a": lambda v: v > 0.10,
    "cagr_actions_5a": lambda v: v > 0.10,
}
COUVERTURE_MIN = 0.5            # règle 2 : sous 50 % du poids applicable, pas de note

# Règle 2 du mémoire : une valeur NON INTERPRÉTABLE n'est pas une valeur MANQUANTE. Un PER
# en perte, une croissance mesurée depuis une base négative, une couverture des intérêts
# sans dette n'ont pas de sens : le critère sort du calcul ET du dénominateur, son poids
# va aux autres. Une donnée absente — historique trop court, champ non déclaré, groupe
# trop petit — fait au contraire baisser la couverture. Les pertes restent pénalisées
# par les marges, dans l'axe qualité. Ces raisons sont celles que posent les formules
# ci-dessus, au caractère près.
SANS_OBJET_NOTE = frozenset({
    "résultat négatif sur 12 mois",
    "EBITDA négatif",
    "flux de trésorerie libre négatif",
    "FFO négatif",
    "capitaux propres négatifs",
    "capitaux investis négatifs",
    "pas de chiffre d'affaires",
    "valeur de départ ou d'arrivée négative ou nulle",
    "aucune dette",
    "pas de charge d'intérêts",
    "génère du cash",
})


def sans_objet_note(raison: Optional[str]) -> bool:
    return raison is not None and (raison in SANS_OBJET_NOTE or raison.startswith("sans objet"))
AXES_MIN_GLOBALE = 3            # règle 3 : pas de note globale sous trois axes notés

# Profil en mots (règle 3) : descriptif et relatif aux sociétés comparables.
PROFIL_MOTS = {
    "qualite": ("fondamentaux fragiles", "fondamentaux dans la moyenne", "fondamentaux solides"),
    "valorisation": ("valorisation élevée", "valorisation dans la moyenne", "valorisation modeste"),
    "croissance": ("croissance faible", "croissance dans la moyenne", "croissance forte"),
    "dilution": ("dilution forte", "dilution modérée", "dilution faible"),
}
SEUILS_MOTS = (3.5, 6.5)        # note sur 10 : sous 3,5 / entre / au-dessus de 6,5


def _note_historique(z: float) -> float:
    """Position face à l'historique en note : 5 à la médiane, ~8,3 au 10e centile."""
    return max(0.0, min(10.0, 5.0 - z * 10.0 / 3.0))


def reference_notee(profil: str, raisons: dict) -> str:
    """
    Multiple de référence de la note : le premier de REFERENCE_VALORISATION qui a un
    sens pour la société — prix sur ventes pour une société en perte, comme le verdict.
    Sans ce repli, le PER sorti du calcul laissait la comparaison au secteur et
    l'historique à poids égaux ; chez une valeur passée par une bulle, l'historique
    flattait la note (IonQ, 4,5 en valorisation à plus de 50 fois ses ventes).
    """
    liste = REFERENCE_VALORISATION.get(profil, ("per",))
    for m in liste:
        if profil in PAR_ID[m].profils and not sans_objet_note(raisons.get(m)):
            return m
    return liste[0]


def noter(profil: str, valeurs: dict, positions: dict, verdict_z: Optional[float],
          raisons: Optional[dict] = None) -> dict:
    """
    {axe: (note sur 10 | None, couverture 0-1, nombre de critères notés)} et 'globale'.

    `valeurs`   : {metric_id: valeur}, pour les seuils absolus ;
    `positions` : {metric_id: percentile 0-100} ;
    `verdict_z` : position du multiple de référence face à son historique ;
    `raisons`   : {metric_id: raison} des valeurs absentes.

    Une métrique sans objet pour le profil sort du calcul ET du dénominateur : ce n'est
    pas un manque d'information. Une valeur non interprétable — un PER en perte — non
    plus (SANS_OBJET_NOTE). Une donnée réellement absente — historique trop court, groupe
    trop petit — réduit la couverture et son poids va aux autres (règle 2).
    """
    raisons = raisons or {}
    ref = reference_notee(profil, raisons)
    out = {}
    for axe in AXES_NOTES:
        total = poids_notes = somme = 0.0
        n = 0
        for mid, poids, sens in NOTATION[axe]:
            if mid == HISTO_VALO:
                total += poids
                if verdict_z is not None:
                    somme += poids * _note_historique(verdict_z); poids_notes += poids; n += 1
                continue
            vrai = ref if mid == REF_VALO else mid
            # Le repli (prix sur ventes d'une société en perte) a déjà pris le poids du
            # multiple de référence : il ne compte pas une seconde fois.
            if mid != REF_VALO and mid == ref:
                continue
            if profil not in PAR_ID[vrai].profils or sans_objet_note(raisons.get(vrai)):
                continue
            total += poids
            p = positions.get(vrai)
            if p is None:
                continue
            note = p / 10.0 if sens == HAUT else (100 - p) / 10.0
            seuil = SEUILS_ABSOLUS.get(vrai)
            v = valeurs.get(vrai)
            if seuil and v is not None and seuil(v):
                note = min(note, PLAFOND_ABSOLU)
            somme += poids * note; poids_notes += poids; n += 1
        couverture = poids_notes / total if total else 0.0
        note_axe = somme / poids_notes if poids_notes and couverture >= COUVERTURE_MIN else None
        out[axe] = (note_axe, couverture, n)
    notees = [out[a][0] for a in AXES_NOTES if out[a][0] is not None]
    out["globale"] = ((sum(notees) / len(notees)) if len(notees) >= AXES_MIN_GLOBALE else None,
                      len(notees) / len(AXES_NOTES), len(notees))
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
        "reference_valorisation": REFERENCE_VALORISATION,
        "profil_mots": PROFIL_MOTS,
        "seuils_mots": SEUILS_MOTS,
        # Critères de chaque note, pour les déplier sous l'axe (règle 3) ; le calcul
        # reste ici, l'interface ne fait que les lister.
        "sans_objet_note": sorted(SANS_OBJET_NOTE),
        "notation": {axe: [{"id": m, "poids": p, "sens": sens} for m, p, sens in crit]
                     for axe, crit in NOTATION.items()},
        "metriques": [
            {"id": m.id, "libelle": m.libelle, "axe": m.axe, "format": m.format,
             "serie": m.serie_annuelle, "perte": m.perte, "note": m.note,
             "explication": EXPLICATIONS[m.id]}
            for m in METRIQUES
        ],
    }, ensure_ascii=False, indent=1))
