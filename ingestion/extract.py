"""
extract.py — Moteur d'extraction companyfacts -> lignes de la table `fundamentals`.

Ne connaît aucun nom de balise : toute la configuration vient de mapping.py.

Ce que le moteur traite, dans l'ordre :
  1. Déduplication des retraitements  : pour un (concept, start, end), le `filed` le plus récent gagne.
  2. Classification des durées        : instant / Q / H1 / 9M / FY, par nombre de jours, jamais par `fp`.
  3. Assemblage par accession         : une ligne se construit d'abord depuis un seul dépôt,
                                        pour que revenue et net_income ne viennent pas de deux
                                        exercices différents. Les compléments sont comptés.
  4. Dérivation du Q4                 : jamais publié. Q4 = FY - 9M, à défaut FY - Q1 - Q2 - Q3.
  5. Fallbacks calculés               : appliqués après la résolution par balise.
  6. Log                              : chaque champ non résolu est tracé avec sa cause.
"""

from collections import defaultdict
from datetime import date
from dataclasses import dataclass, field as dc_field
import mapping as M

# --- classification des durées (en jours) ------------------------------------
DURATIONS = {"Q": (75, 105), "H1": (165, 195), "M9": (255, 285), "FY": (330, 400)}


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _duration_kind(start, end):
    if not start:
        return "INSTANT"
    n = _days(start, end)
    for k, (lo, hi) in DURATIONS.items():
        if lo <= n <= hi:
            return k
    return None                      # durée atypique : ignorée


# --- log ---------------------------------------------------------------------
@dataclass
class ExtractionLog:
    cik: int = 0
    ticker: str = ""
    profile: str = ""
    unresolved: dict = dc_field(default_factory=lambda: defaultdict(list))   # champ -> [périodes]
    resolved_by: dict = dc_field(default_factory=dict)                        # champ -> balise retenue
    derived: set = dc_field(default_factory=set)
    inferred_zero: set = dc_field(default_factory=set)
    mixed_accession: int = 0
    fields_total: int = 0
    fields_from_primary: int = 0
    unmapped_frequent_tags: list = dc_field(default_factory=list)
    scale_fixed: int = 0
    periods: int = 0
    fiscal_years: int = 0

    def miss(self, fieldname, period):
        self.unresolved[fieldname].append(period)


# --- indexation d'un companyfacts -------------------------------------------
def index_facts(companyfacts: dict):
    """
    Aplatit companyfacts en {concept: {(kind, end, start): fact}} après déduplication
    des retraitements. Retourne aussi l'ensemble des concepts vus, pour le log.
    """
    idx = defaultdict(dict)
    seen_tags = set()
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    for tag, node in gaap.items():
        seen_tags.add(tag)
        for unit, entries in node["units"].items():
            if unit not in (M.USD, M.SHARES, M.PER_SHARE):
                continue
            for e in entries:
                if e.get("form") not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                    continue
                kind = _duration_kind(e.get("start"), e["end"])
                if kind is None:
                    continue
                key = (kind, e["end"], e.get("start"))
                prev = idx[tag].get(key)
                # Retraitement : le dépôt le plus récent l'emporte.
                if prev is None or (e["filed"], e["accn"]) > (prev["filed"], prev["accn"]):
                    idx[tag][key] = e
    return idx, seen_tags


MIN_CONCEPTS_PER_PERIOD = 10   # une clôture réelle est balisée par des dizaines de concepts


def _periods(idx):
    """
    Inventaire des périodes réelles : {(kind, end): {start}}.

    Une poignée de balises isolées peut porter une durée annuelle à une date qui n'est
    pas une clôture (constaté sur UNP : une période fantôme au 2026-03-31, portée par
    un seul concept, vidait le dernier exercice). On exige donc qu'une période soit
    déclarée par au moins MIN_CONCEPTS_PER_PERIOD concepts distincts.
    """
    count = defaultdict(set)
    starts = defaultdict(set)
    for tag, facts in idx.items():
        for kind, end, start in facts:
            count[(kind, end)].add(tag)
            starts[(kind, end)].add(start)
    return {k: starts[k] for k, tags in count.items() if len(tags) >= MIN_CONCEPTS_PER_PERIOD}


# --- résolution d'un champ ---------------------------------------------------
def _accessions_for(idx, kind, end, starts, chains=()):
    """
    Accessions ayant déclaré cette période, classées par capacité à renseigner la ligne.

    Prendre simplement le dépôt le plus récent ne marche pas : un 10-Q qui cite une seule
    valeur comparative d'un exercice ancien passerait devant le 10-K qui le détaille
    (mesuré : 54 % de cohérence). On classe donc par nombre de champs cibles couverts,
    puis par date de dépôt.
    """
    covers = defaultdict(int)
    filed = {}
    starts = tuple(starts) if not isinstance(starts, (str, type(None))) else (starts,)
    tags_of_interest = {t for c in chains for t in c}
    for tag, facts in idx.items():
        for st in starts:
            f = facts.get((kind, end, st))
            if f is None:
                continue
            filed[f["accn"]] = f["filed"]
            if tag in tags_of_interest:
                covers[f["accn"]] += 1
    return sorted(filed, key=lambda a: (covers.get(a, 0), filed[a]), reverse=True)


def _lookup(idx, chain, kind, end, starts, accn_order=()):
    """
    Parcourt la chaîne de balises, en privilégiant une accession donnée.

    L'assemblage se fait d'abord depuis le dépôt le plus récent couvrant la période,
    pour que revenue et net_income d'une même ligne viennent du même document. Un 10-K
    ne présente que 2 exercices de bilan contre 3 de compte de résultat : le recours à
    une accession plus ancienne est normal et compté, pas suspect.

    `starts` est un ENSEMBLE de dates de début, pas une seule. Un même exercice peut
    être daté différemment selon le dépôt — Garmin clôture en semaines fiscales, donc
    2023-12-31 chez l'un et 2024-01-01 chez l'autre pour le même exercice. Retenir
    arbitrairement le plus ancien faisait échouer la recherche des balises indexées
    sur l'autre : deux exercices de Garmin ressortaient entièrement vides.
    Retourne (valeur, balise, accn, filed, rang_accession) ou None.
    """
    starts = tuple(starts) if not isinstance(starts, (str, type(None))) else (starts,)
    for rank, accn in enumerate(accn_order):
        for tag in chain:
            for st in starts:
                f = idx.get(tag, {}).get((kind, end, st))
                if f is not None and f["accn"] == accn:
                    return f["val"], tag, f["accn"], f["filed"], rank
    for tag in chain:                       # filet : accession hors liste
        for st in starts:
            f = idx.get(tag, {}).get((kind, end, st))
            if f is not None:
                return f["val"], tag, f["accn"], f["filed"], 99
    return None


def _resolve_period(idx, profile, kind, end, start, log, period_label):
    """
    Construit un dict {champ: valeur} pour une période.
    Privilégie l'accession la plus récente, complète depuis les autres et le compte.
    """
    values, provenance = {}, {}
    chains_flow = [M.BY_NAME[n].chain(profile) for n in M.ORDER
                   if M.applicable(n, profile) and M.BY_NAME[n].kind == M.FLOW]
    chains_stock = [M.BY_NAME[n].chain(profile) for n in M.ORDER
                    if M.applicable(n, profile) and M.BY_NAME[n].kind == M.STOCK]
    order_flow = _accessions_for(idx, kind, end, start, chains_flow)
    order_stock = _accessions_for(idx, "INSTANT", end, (None,), chains_stock)
    for name in M.ORDER:
        f = M.BY_NAME[name]
        if not M.applicable(name, profile):
            continue
        wkind = "INSTANT" if f.kind == M.STOCK else kind
        wstart = (None,) if f.kind == M.STOCK else start
        worder = order_stock if f.kind == M.STOCK else order_flow
        hit = _lookup(idx, f.chain(profile), wkind, end, wstart, worder) if f.tags else None
        if hit is not None:
            val, tag, accn, filed, _rank = hit
            values[name] = val * f.sign
            provenance[name] = (tag, accn, filed)
            log.resolved_by.setdefault(name, tag)
        elif f.derive is not None:
            v = f.derive(values)
            if v is not None:
                values[name] = v
                log.derived.add(name)
            else:
                values[name] = None
                log.miss(name, period_label)
        else:
            values[name] = None
            log.miss(name, period_label)

    # inférence à zéro, sous garde-fou (voir mapping.INFER_ZERO)
    inferred = set()
    for name, (profiles, required, guard, never_tagged) in M.INFER_ZERO.items():
        if not M.applicable(name, profile) or profile not in profiles:
            continue
        if values.get(name) is not None:
            continue
        if not all(values.get(r) is not None for r in required):
            continue
        if guard is not None and not guard(values):
            continue
        if never_tagged and any(t in idx for t in M.BY_NAME[name].chain(profile)):
            continue
        values[name] = 0.0
        inferred.add(name)
        log.inferred_zero.add(name)
        if period_label in log.unresolved.get(name, []):
            log.unresolved[name].remove(period_label)
    values["_inferred_zero"] = sorted(inferred)

    # Cohérence mesurée état par état : un 10-K présente 3 exercices de compte de
    # résultat mais seulement 2 de bilan, les deux primaires diffèrent légitimement.
    pf = order_flow[0] if order_flow else None
    ps = order_stock[0] if order_stock else None
    from_primary = sum(
        1 for n, p in provenance.items()
        if p[1] == (ps if M.BY_NAME[n].kind == M.STOCK else pf)
    )
    accns = {p[1] for p in provenance.values()}
    log.fields_total += len(provenance)
    log.fields_from_primary += from_primary
    if len(accns) > 1:
        log.mixed_accession += 1
    primary = max(provenance.values(), key=lambda p: p[2])[1] if provenance else None
    filed = max((p[2] for p in provenance.values()), default=None)
    return values, primary, filed, len(accns)


# Grandeurs de flux qui ne s'additionnent PAS d'un trimestre à l'autre. Une moyenne
# pondérée d'actions sur l'exercice moins celle sur neuf mois ne donne rien : constaté
# en base, des Q4 reconstruits à -5,0 M d'actions pour 3M, 0,1 M pour d'autres. Le BPA
# est conservé — FY moins 9 mois est l'approximation d'usage, y compris chez les
# fournisseurs commerciaux — mais les nombres d'actions restent vides sur les Q4.
NON_ADDITIVE = {"shares_diluted", "shares_basic"}


def _derive_q4(fy_values, m9_values, quarters):
    """
    Q4 n'est jamais publié. On le reconstruit sur les grandeurs de flux uniquement :
    les grandeurs de bilan à la clôture annuelle SONT déjà celles du Q4.
    """
    out = dict(fy_values)
    for name in M.ORDER:
        f = M.BY_NAME.get(name)
        if f is None or f.kind != M.FLOW:
            continue
        if name in NON_ADDITIVE:
            out[name] = None
            continue
        fy = fy_values.get(name)
        if fy is None:
            out[name] = None
            continue
        if m9_values and m9_values.get(name) is not None:
            out[name] = fy - m9_values[name]
        elif quarters and all(q.get(name) is not None for q in quarters) and len(quarters) == 3:
            out[name] = fy - sum(q[name] for q in quarters)
        else:
            out[name] = None
    # per-share : ne se soustrait pas, on le laisse à None plutôt que de produire un faux
    out["eps_diluted"] = None if not m9_values else out.get("eps_diluted")
    return out


# --- ruptures d'échelle ------------------------------------------------------
# Certains déposants publient leurs nombres d'actions EN MILLIERS tout en déclarant
# l'unité `shares`. Constaté chez Garmin : le 10-K de 2024 donne 192 058 pour
# l'exercice 2023, les dépôts suivants 192 058 000. La déduplication par `filed`
# rattrape les exercices récents, mais les anciens n'existent que dans les dépôts à
# l'ancienne échelle — d'où un facteur 1000 au milieu d'une même série, et une CAGR
# de dilution de +900 %/an là où il n'y a eu aucune émission.
# Mesuré sur 1 393 sociétés : 4,7 % touchées, dont ConocoPhillips et Under Armour.
#
# La référence de contrôle est interne au dépôt : résultat net / BPA dilué donne le
# nombre d'actions impliqué, les deux grandeurs venant du même document.
FACTEURS_ECHELLE = (1e3, 1e6)
CHAMPS_ACTIONS = ("shares_diluted", "shares_basic")


def _fix_scale(rows, log):
    """Corrige les ruptures d'échelle sur les nombres d'actions, en deux temps."""
    valides = {}                       # période -> ordre de grandeur validé

    # 1. contrôle par l'identité résultat net / BPA, quand les deux existent
    for r in rows:
        ni, eps = r.get("net_income"), r.get("eps_diluted")
        if ni is None or eps is None or abs(eps) < 0.01:
            continue
        implique = abs(ni / eps)
        if implique <= 0:
            continue
        for champ in CHAMPS_ACTIONS:
            sh = r.get(champ)
            if not sh or sh <= 0:
                continue
            if 0.5 < sh / implique < 2:            # cohérent, rien à faire
                valides[(r["period_end"], champ)] = sh
                continue
            for f in FACTEURS_ECHELLE:
                if 0.8 < (sh * f) / implique < 1.25:
                    r[champ] = sh * f
                    valides[(r["period_end"], champ)] = sh * f
                    log.scale_fixed += 1
                    r.setdefault("_scale_corrected", []).append(f"{champ}:x{int(f)}")
                    break

    # 2. propagation par continuité, pour les exercices sans BPA exploitable
    for champ in CHAMPS_ACTIONS:
        connus = sorted((p, v) for (p, c), v in valides.items() if c == champ)
        if not connus:
            continue
        for r in rows:
            sh = r.get(champ)
            if not sh or sh <= 0 or (r["period_end"], champ) in valides:
                continue
            ref = min(connus, key=lambda kv: abs(
                (date.fromisoformat(kv[0]) - date.fromisoformat(r["period_end"])).days))[1]
            for f in FACTEURS_ECHELLE:
                if 0.5 < (sh * f) / ref < 2 and not 0.5 < sh / ref < 2:
                    r[champ] = sh * f
                    log.scale_fixed += 1
                    r.setdefault("_scale_corrected", []).append(f"{champ}:x{int(f)}")
                    break
    return rows


# --- point d'entrée ----------------------------------------------------------
def extract(companyfacts: dict, cik: int, ticker: str, profile: str):
    """Retourne (lignes, log). Une ligne = un dict prêt pour l'upsert `fundamentals`."""
    log = ExtractionLog(cik=cik, ticker=ticker, profile=profile)
    idx, seen = index_facts(companyfacts)
    periods = _periods(idx)

    fy_ends = sorted({end for (k, end) in periods if k == "FY"})
    q_ends = sorted({end for (k, end) in periods if k == "Q"})
    log.fiscal_years = len(fy_ends)

    rows = []
    by_end = {}
    for end in fy_ends:
        starts = tuple(sorted(s for s in periods[("FY", end)] if s)) or (None,)
        vals, accn, filed, n_accn = _resolve_period(idx, profile, "FY", end, starts, log, f"FY {end}")
        by_end[("FY", end)] = vals
        rows.append(dict(vals, cik=cik, period_end=end, fiscal_period="FY",
                         accn=accn, filed=filed, n_accessions=n_accn))
    for end in q_ends:
        starts = tuple(sorted(s for s in periods[("Q", end)] if s)) or (None,)
        vals, accn, filed, n_accn = _resolve_period(idx, profile, "Q", end, starts, log, f"Q {end}")
        by_end[("Q", end)] = vals
        rows.append(dict(vals, cik=cik, period_end=end, fiscal_period="Q",
                         accn=accn, filed=filed, n_accessions=n_accn))

    # Q4 reconstruit
    for end in fy_ends:
        if ("Q", end) in by_end:
            continue                                    # déjà publié (rare)
        m9 = None
        for (k, e), starts in periods.items():
            if k == "M9" and e < end and _days(e, end) < 100:
                st = tuple(sorted(x for x in starts if x)) or (None,)
                m9, _, _, _ = _resolve_period(idx, profile, "M9", e, st, log, f"M9 {e}")
                break
        qs = [by_end[("Q", e)] for e in q_ends if e < end and _days(e, end) < 295]
        q4 = _derive_q4(by_end[("FY", end)], m9, qs[-3:] if len(qs) >= 3 else None)
        if any(q4.get(n) is not None for n in ("revenue", "net_income")):
            rows.append(dict(q4, cik=cik, period_end=end, fiscal_period="Q",
                             accn=None, filed=None, n_accessions=0, q4_derived=True))

    _fix_scale(rows, log)
    log.periods = len(rows)

    # balises fréquentes non mappées : matière première de la prochaine itération
    mapped = {t for f in M.FIELDS for p in M.ALL for t in f.chain(p)}
    log.unmapped_frequent_tags = sorted(
        t for t in seen if t not in mapped and len(idx.get(t, {})) >= 8
    )
    return rows, log


def data_quality_score(rows, profile: str) -> float:
    """
    Part des champs applicables réellement renseignés sur les 3 derniers exercices.
    Ce n'est pas une note de qualité de l'entreprise : c'est la couverture de sa donnée.
    """
    fys = [r for r in rows if r["fiscal_period"] == "FY"]
    fys = sorted(fys, key=lambda r: r["period_end"])[-3:]
    if not fys:
        return 0.0
    names = [n for n in M.STORED if M.applicable(n, profile)]
    filled = sum(1 for r in fys for n in names if r.get(n) is not None)
    return round(filled / (len(fys) * len(names)), 3)
