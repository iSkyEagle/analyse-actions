"""
mapping.py — Correspondance concepts XBRL US GAAP -> champs de la table `fundamentals`.

Fichier de configuration unique. Toute la connaissance du désordre des balises US GAAP
vit ici et nulle part ailleurs. Le moteur (extract.py) ne connaît aucun nom de balise.

Trois mécanismes, dans cet ordre de priorité :
  1. CHAÎNE DE BALISES : liste ordonnée, la première présente gagne.
     L'ordre vient d'un recensement réel sur 44 déposants 10-K/10-Q, pas d'une intuition.
  2. PRÉFÉRENCE PAR PROFIL : une banque n'a pas le même chef de file qu'un industriel.
  3. FALLBACK CALCULÉ : quand aucune balise n'existe mais que la grandeur se déduit
     d'autres champs déjà résolus. Toujours tracé comme tel dans le log.

Un champ non résolu vaut NULL, jamais 0 — sauf `total_debt`, seul cas d'inférence à zéro,
et uniquement sous les gardes-fous declares dans INFER_ZERO.
"""

from dataclasses import dataclass, field as dc_field
from typing import Callable, Optional

# --- unités et natures -------------------------------------------------------
USD, SHARES, PER_SHARE, PURE = "USD", "shares", "USD/shares", "pure"
FLOW = "flow"    # grandeur de période : a un `start` et un `end`
STOCK = "stock"  # grandeur d'instant : n'a qu'un `end`

# --- profils de mapping (dérivés du SIC, cf. companies.mapping_profile) -------
STD, FIN, REIT = "standard", "financial", "reit"
ALL = (STD, FIN, REIT)


@dataclass(frozen=True)
class Field:
    name: str
    unit: str
    kind: str
    tags: tuple = ()                      # chaîne de fallback, ordre = priorité
    profiles: tuple = ALL                 # profils où le champ est applicable
    prefer: dict = dc_field(default_factory=dict)   # {profil: (balises prioritaires,)}
    sign: int = 1                         # -1 si la balise est de signe opposé à la convention
    derive: Optional[Callable] = None     # fallback calculé, reçoit le dict des champs déjà résolus
    note: str = ""

    def chain(self, profile: str) -> tuple:
        """Chaîne de balises effective pour un profil donné."""
        return tuple(self.prefer.get(profile, ())) + tuple(
            t for t in self.tags if t not in self.prefer.get(profile, ())
        )


def _sub(a, b):
    """Soustraction tolérante : None si l'un des deux termes manque."""
    return None if a is None or b is None else a - b


def _add(a, b):
    return None if a is None or b is None else a + b


# =============================================================================
# COMPTE DE RÉSULTAT
# =============================================================================
INCOME = [
    Field(
        "revenue", USD, FLOW,
        # 30/44 sur la balise ASC 606 seule ; 43/44 avec la chaîne complète.
        # SalesRevenueNet est indispensable : elle couvre les exercices pré-2018.
        tags=(
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            "SalesRevenueServicesNet",
        ),
        prefer={
            FIN: ("RevenuesNetOfInterestExpense", "Revenues", "InterestAndDividendIncomeOperating"),
            REIT: ("RealEstateRevenueNet", "Revenues"),
        },
        note="raccord ASC 605 -> ASC 606 : la chaîne est temporelle autant qu'inter-émetteurs",
    ),
    Field(
        "cogs", USD, FLOW, profiles=(STD,),
        tags=("CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold", "CostOfServices"),
        note="27/44 — beaucoup de sociétés de services ne ventilent pas le coût des ventes",
    ),
    Field(
        "gross_profit", USD, FLOW, profiles=(STD,),
        tags=("GrossProfit",),
        # 16/44 sur la balise seule, 27/44 avec le calcul.
        derive=lambda f: _sub(f.get("revenue"), f.get("cogs")),
        note="non applicable aux banques et foncières par construction",
    ),
    Field(
        "operating_income", USD, FLOW,
        tags=("OperatingIncomeLoss",),
        prefer={
            FIN: ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
        },
        note="les banques ne publient pas de résultat opérationnel : repli sur le résultat avant impôt",
    ),
    Field(
        "net_income", USD, FLOW,
        tags=("NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"),
        prefer={FIN: ("NetIncomeLossAvailableToCommonStockholdersBasic", "NetIncomeLoss")},
        note="NetIncomeLoss = part du groupe ; ProfitLoss = y compris minoritaires",
    ),
    Field(
        "eps_diluted", PER_SHARE, FLOW,
        tags=("EarningsPerShareDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"),
    ),
    Field(
        "shares_diluted", SHARES, FLOW,
        # Balise combinée : une société en perte, dont le nombre dilué égale le nombre de
        # base, le déclare souvent sous une balise unique. Absente de la chaîne, elle
        # privait de nombre d'actions précisément les sociétés déficitaires, celles où
        # la dilution compte le plus — Caribou en 2019 et 2020.
        tags=("WeightedAverageNumberOfDilutedSharesOutstanding",
              "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
              "WeightedAverageNumberOfSharesOutstandingBasic"),
        note="pivot de l'axe Dilution : 44/44, la métrique différenciante est la mieux couverte",
    ),
    Field(
        "sbc", USD, FLOW,
        tags=("ShareBasedCompensation",
              "AllocatedShareBasedCompensationExpense",
              "ShareBasedCompensationArrangementByShareBasedPaymentAwardCompensationCost1"),
    ),
    Field(
        "d_and_a", USD, FLOW,
        tags=("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
              "DepreciationAndAmortization", "Depreciation"),
    ),
    Field(
        "interest_expense", USD, FLOW,
        tags=("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt",
              "InterestAndDebtExpense", "InterestExpenseBorrowings",
              "InterestPaidNet", "InterestPaid"),
        prefer={FIN: ("InterestIncomeExpenseNet", "InterestExpense")},
        note="dernier recours: InterestPaidNet (42/44), decaisse et non charge — approximation tracee",
    ),
    Field(
        "shares_basic", SHARES, FLOW,
        tags=("WeightedAverageNumberOfSharesOutstandingBasic",
              "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
              "WeightedAverageNumberOfDilutedSharesOutstanding"),
        note="l'ecart basic/dilue est une mesure directe de l'overhang"),
    Field(
        "income_tax", USD, FLOW, tags=("IncomeTaxExpenseBenefit",),
        note="43/44 — indispensable au NOPAT donc au ROIC (axe Qualite du memoire)"),
    Field(
        "rd_expense", USD, FLOW, profiles=(STD,),
        tags=("ResearchAndDevelopmentExpense",
              "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
    ),
]

# =============================================================================
# BILAN
# =============================================================================
BALANCE = [
    Field("total_assets", USD, STOCK, tags=("Assets",)),
    Field(
        "equity", USD, STOCK,
        tags=("StockholdersEquity",
              "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ),
    Field("_lse", USD, STOCK, tags=("LiabilitiesAndStockholdersEquity",),
          note="champ technique, non stocké : sert au calcul de total_liabilities"),
    Field(
        "total_liabilities", USD, STOCK,
        tags=("Liabilities",),
        # 38/44 sur la balise seule, 44/44 avec le calcul. Six grandes capitalisations
        # (INTC, VZ, WMT, MAR, MNST, SXT) ne balisent jamais `Liabilities`.
        derive=lambda f: _sub(f.get("_lse"), f.get("equity")),
    ),
    Field(
        "cash", USD, STOCK,
        tags=("CashAndCashEquivalentsAtCarryingValue",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    ),
    Field(
        "debt_lt", USD, STOCK,
        tags=("LongTermDebtNoncurrent", "LongTermDebt",
              "LongTermDebtAndCapitalLeaseObligations", "DebtLongtermAndShorttermCombinedAmount"),
    ),
    Field(
        "debt_st", USD, STOCK,
        tags=("LongTermDebtCurrent", "ShortTermBorrowings",
              "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent"),
    ),
    Field("current_assets", USD, STOCK, profiles=(STD,), tags=("AssetsCurrent",)),
    Field("current_liabilities", USD, STOCK, profiles=(STD,), tags=("LiabilitiesCurrent",)),
    Field("inventory", USD, STOCK, profiles=(STD,), tags=("InventoryNet", "InventoryGross")),
    Field("receivables", USD, STOCK, profiles=(STD,),
          tags=("AccountsReceivableNetCurrent", "ReceivablesNetCurrent")),
    Field("retained_earnings", USD, STOCK, tags=("RetainedEarningsAccumulatedDeficit",)),
    Field("ppe_net", USD, STOCK,
          tags=("PropertyPlantAndEquipmentNet",
                "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
                "PropertyPlantAndEquipmentExcludingLessorAssetUnderOperatingLeaseAfterAccumulatedDepreciation"),
          prefer={REIT: ("RealEstateInvestmentPropertyNet", "PropertyPlantAndEquipmentNet")},
          note="post-ASC 842, la ligne de bilan agrege PP&E et droits d'usage : "
               "sans la balise combinee, INTC META MU TSLA GM UNP ressortaient vides"),
]

# =============================================================================
# FLUX DE TRÉSORERIE
# =============================================================================
CASHFLOW = [
    Field(
        "ocf", USD, FLOW,
        tags=("NetCashProvidedByUsedInOperatingActivities",
              "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    ),
    Field(
        "capex", USD, FLOW,
        tags=("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsForCapitalImprovements", "PaymentsToAcquireOtherPropertyPlantAndEquipment"),
        note="déposé en valeur positive (décaissement) : FCF = ocf - capex",
    ),
    Field("cff", USD, FLOW, tags=("NetCashProvidedByUsedInFinancingActivities",)),
    Field("dividends_paid", USD, FLOW,
          tags=("PaymentsOfDividendsCommonStock", "PaymentsOfDividends",
                "PaymentsOfOrdinaryDividends")),
    Field("buybacks", USD, FLOW,
          tags=("PaymentsForRepurchaseOfCommonStock", "PaymentsForRepurchaseOfEquity",
                "TreasuryStockValueAcquiredCostMethod")),
    Field("issuance", USD, FLOW,
          tags=("ProceedsFromIssuanceOfCommonStock", "ProceedsFromIssuanceOrSaleOfEquity",
                "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlansIncludingStockOptions",
                "ProceedsFromIssuanceOfSharesUnderIncentiveAndShareBasedCompensationPlans",
                "ProceedsFromStockOptionsExercised", "ProceedsFromStockPlans")),
]

# =============================================================================
# GRANDEURS PUREMENT CALCULÉES (aucune balise, jamais)
# =============================================================================
COMPUTED = [
    Field("ebitda", USD, FLOW,
          derive=lambda f: _add(f.get("operating_income"), f.get("d_and_a")),
          note="38/44 — approximation assumée : EBIT + D&A, pas l'EBITDA ajusté du management"),
    Field("fcf", USD, FLOW,
          derive=lambda f: _sub(f.get("ocf"), f.get("capex")),
          note="42/44"),
    Field("total_debt", USD, STOCK,
          derive=lambda f: (f.get("debt_lt") or 0) + (f.get("debt_st") or 0)
                           if (f.get("debt_lt") is not None or f.get("debt_st") is not None) else None),
]

FIELDS = INCOME + BALANCE + CASHFLOW + COMPUTED
BY_NAME = {f.name: f for f in FIELDS}
ORDER = [f.name for f in FIELDS]           # ordre de résolution : les dérivés arrivent après leurs sources
STORED = [n for n in ORDER if not n.startswith("_")]

# --- inférence à zéro : le seul endroit où une absence devient une valeur ----------
# Une société sans dette ne balise rien : l'absence est indiscernable d'un trou de mapping.
# Première version de la règle (bilan complet suffit) : 27 inférences, dont 10 fausses —
# toutes les banques, GM (balises d'extension hors us-gaap), et les capitaux propres négatifs.
# Règle resserrée : profil standard uniquement, et passif total inférieur aux capitaux
# propres. Sur l'échantillon, élimine les 10 faux et conserve les 17 vrais.
# Chaque inférence est stockée dans `inferred_zero` de la ligne : le front doit la signaler.
def _no_leverage(f):
    tl, eq = f.get("total_liabilities"), f.get("equity")
    return tl is not None and eq is not None and eq > 0 and tl < eq

# `never_tagged` : n'inférer zéro que si aucune balise de la chaîne n'apparaît nulle part
# dans l'historique de la société. C'est le test qui distingue « ne fait pas de R&D »
# de « n'a pas balisé sa R&D cette année-là ». Vérifié sur l'échantillon : les 22 sociétés
# sans aucune balise R&D sont toutes du commerce, du transport, de l'hôtellerie, de la
# banque ou des foncières — aucun faux positif technologique ou pharmaceutique.
INFER_ZERO = {
    # champ            : (profils autorisés, champs requis, garde-fou, never_tagged)
    "total_debt":        ((STD,),             ("total_assets", "equity", "total_liabilities"), _no_leverage, False),
    "dividends_paid":    ((STD, FIN, REIT),   ("ocf",), None, False),
    "buybacks":          ((STD, FIN, REIT),   ("ocf",), None, False),
    "issuance":          ((STD, FIN, REIT),   ("ocf",), None, False),
    "rd_expense":        ((STD,),             ("revenue", "operating_income"), None, True),
}

# =============================================================================
# PROFILS : champs volontairement non applicables (exclus, pas notés 0 — règle 2 du mémoire)
# =============================================================================
NOT_APPLICABLE = {
    FIN: {"cogs", "gross_profit", "capex", "inventory", "current_assets",
          "current_liabilities", "ebitda", "fcf", "rd_expense"},
    REIT: {"cogs", "gross_profit", "inventory", "current_assets",
           "current_liabilities", "rd_expense"},
    STD: set(),
}


def applicable(field_name: str, profile: str) -> bool:
    f = BY_NAME[field_name]
    return profile in f.profiles and field_name not in NOT_APPLICABLE.get(profile, set())


# Immeubles de placement : une société de l'immobilier n'est une foncière que si ses
# immeubles forment l'essentiel de son actif. Mesuré en octobre 2026 : 55 à 130 % de
# l'actif chez les foncières (Realty Income, Invitation Homes, Gladstone…), 0 % chez les
# prestataires (CBRE, JLL, Compass, Opendoor). Plus de 100 % : les immeubles sont
# comptés au coût d'achat, avant amortissement.
_IMMEUBLES = ("RealEstateInvestmentPropertyAtCost", "RealEstateInvestmentPropertyNet",
              "RealEstateInvestmentPropertyNetExcludingConstructionInProgress")
SEUIL_FONCIERE = 0.5


def part_immeubles(facts: dict) -> float:
    """Immeubles de placement / actif total, au dernier 10-K. 0 si non déclarés."""
    gaap = (facts or {}).get("facts", {}).get("us-gaap", {})

    def dernier(tag):
        u = [e for e in gaap.get(tag, {}).get("units", {}).get(USD, []) if e.get("form") == "10-K"]
        return max(u, key=lambda e: (e["end"], e["filed"]))["val"] if u else None

    actif = dernier("Assets")
    immeubles = max((v for v in (dernier(t) for t in _IMMEUBLES) if v is not None), default=None)
    return immeubles / actif if actif and immeubles else 0.0


def profile_from_sic(sic, facts: dict = None) -> str:
    """
    companies.mapping_profile, dérivé du SIC et, pour l'immobilier, des comptes.

    Révisé le 12 octobre 2026. Les tranches de codes seules classaient mal deux familles :
    - les gestionnaires de soins (6324 : UnitedHealth, Elevance, Cigna) recevaient le
      profil financier d'une banque, et perdaient marges, ROIC et multiples de valeur
      d'entreprise ; les assureurs du 6321 (Aflac, Principal) restent financiers ;
    - toute la tranche 6500-6799 recevait le profil foncière, y compris des prestataires
      immobiliers (CBRE, JLL), des promoteurs, des détenteurs de brevets (Dolby,
      InterDigital) et de redevances (Royal Gold, Texas Pacific Land).

    Le code 6798 désigne toujours une foncière. Mais des foncières déposent aussi sous
    6500-6599 (Invitation Homes en 6510, Gladstone en 6519) : là, ce sont les comptes qui
    décident. Sans comptes — passage mensuel de l'univers —, le profil reste provisoire,
    et c'est le passage des comptes qui le fixe.
    """
    try:
        n = int(sic or 0)
    except (TypeError, ValueError):
        return STD
    if n == 6770:
        return "spac"          # blank check : hors univers, filtré en amont
    if n == 6324:
        return STD             # gestion des soins : une entreprise de services
    if 6000 <= n < 6500:
        return FIN
    if n == 6798:
        return REIT
    if 6500 <= n < 6600:
        if facts is None:
            return REIT        # provisoire, fixé par le passage des comptes
        return REIT if part_immeubles(facts) >= SEUIL_FONCIERE else STD
    if n in (6792, 6794, 6795):
        return STD             # redevances pétrolières, brevets, redevances minières
    if 6700 <= n < 6800:
        return FIN             # sociétés d'investissement
    return STD


def profil_provisoire(sic) -> bool:
    """Le profil déduit du seul SIC doit-il attendre les comptes ?"""
    try:
        return 6500 <= int(sic or 0) < 6600
    except (TypeError, ValueError):
        return False


# =============================================================================
# PÉRIMÈTRE : ce qui n'est pas mappable par ce fichier
# =============================================================================
# Le discriminant fiable n'est ni le SIC ni le suffixe du ticker : c'est la forme de
# dépôt. Un émetteur qui ne dépose pas majoritairement en 10-K/10-Q relève d'une autre
# taxonomie et d'un autre mapping. Décision de phase 1 : ces émetteurs sont ingérés
# pour leurs cours seulement, avec `mapping_profile` renseigné et fondamentaux vides.
# Marqueurs d'activité opérationnelle. Une fiducie ou un produit indiciel n'a ni
# immobilisations, ni impôt sur les sociétés — ce sont des entités transparentes —,
# ni salariés, ni fournisseurs. Une société opérationnelle en porte au moins un.
MARQUEURS_OPERATIONNELS = (
    "PropertyPlantAndEquipmentNet",
    "IncomeTaxExpenseBenefit",
    "ShareBasedCompensation",
    "AccountsPayableCurrent",
    "OperatingLeaseRightOfUseAsset",
    "EmployeeRelatedLiabilitiesCurrent",
)

# Seuil de concepts, nécessaire mais NON suffisant. Trois versions ont échoué avant
# celle-ci, et chacune échangeait une classe de faux exclus contre une autre :
#
#   « moins de 100 concepts »          -> écartait Avidity Biosciences (95 concepts)
#   « bilan + résultat + CA »          -> écartait les biotechs pré-revenus, 733 exclusions
#   « bilan + résultat + actions dil. »-> écartait Visa et Constellation Brands, dont les
#                                         actions diluées sont balisées par classe via une
#                                         dimension que companyfacts supprime
#
# La règle retenue exige les DEUX conditions : peu de concepts ET aucun marqueur
# opérationnel. Visa déclare 626 concepts, elle ne peut plus être touchée quoi qu'il
# arrive ; Avidity en déclare 95 mais porte 4 marqueurs.
#
# Elle est calibrée à zéro faux exclu, pas à zéro faux inclus : neuf produits indiciels
# passent au travers (CurrencyShares, quelques trusts crypto portant un seul marqueur).
# C'est l'arbitrage voulu. La règle 2 du mémoire les neutralise en aval — couverture
# d'axe sous 50 %, aucun score affiché — alors qu'écarter Visa serait irrattrapable.
SEUIL_CONCEPTS_FAIBLE = 150


def out_of_scope(companyfacts: dict, sic=None):
    """Retourne (profil_hors_perimetre, raison) ou (None, None) si mappable ici."""
    facts = companyfacts.get("facts", {})
    if "ifrs-full" in facts:
        return "ifrs", "taxonomie ifrs-full (20-F / 40-F) : annuel uniquement, pas de trimestres"
    if "cef" in facts:
        return "fund", "taxonomie cef : fonds fermé, pas une action ordinaire"
    gaap = facts.get("us-gaap", {})
    if not gaap:
        return "unknown", "aucun concept us-gaap"
    forms = {}
    for c in gaap.values():
        for u in c["units"].values():
            for e in u:
                forms[e.get("form")] = forms.get(e.get("form"), 0) + 1
    total = sum(forms.values()) or 1
    dom = (forms.get("10-K", 0) + forms.get("10-Q", 0) + forms.get("10-K/A", 0)
           + forms.get("10-Q/A", 0)) / total
    if dom < 0.5:
        return "foreign", f"seulement {dom:.0%} des faits déposés en 10-K/10-Q"
    if profile_from_sic(sic) == "spac":
        return "spac", "blank check (SIC 6770)"
    if (len(gaap) < SEUIL_CONCEPTS_FAIBLE
            and not any(m in gaap for m in MARQUEURS_OPERATIONNELS)):
        return "fund", (f"produit indiciel ou fiducie : {len(gaap)} concepts us-gaap "
                        f"et aucun marqueur d'activité opérationnelle")
    return None, None
