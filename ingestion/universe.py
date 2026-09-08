"""
universe.py — Construction de l'univers de titres.

Le plan initial faisait de l'univers une étape unique. Impossible : deux des quatre
filtres — capitalisation et volume médian — dépendent des cours, qui ne sont ingérés
qu'après. La construction est donc en deux passes séparées par l'ingestion :

    passe A  build_candidates()    EDGAR seul   10 412 -> ~4 800 candidats
             [ingestion des cours et des fondamentaux]
    passe B  finalize_universe()   SQL pur      applique capitalisation et volume

Le `data_quality_score` n'est pas calculé ici non plus : il mesure le taux de complétion
XBRL et suppose donc les fondamentaux déjà mappés. Il est produit par EdgarProvider et
écrit par db.update_company_facts(). La passe B ne fait que le lire.

Coût de la passe A : 4 requêtes. Trois index trimestriels (~130 Mo) plus la liste des
émetteurs, au lieu de 7 711 appels à `submissions`.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Iterator, Optional

from providers.base import FetchError, Issuer

log = logging.getLogger(__name__)

# Formes de dépôt périodiques. 20-F et 40-F servent à *identifier* les déposants
# annuels pour les écarter, pas à les retenir.
PERIODIC = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
ANNUAL_ONLY = {"20-F", "20-F/A", "40-F", "40-F/A"}

INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/form.idx"

# Seuils du mémoire. Le plancher d'ingestion n'est pas le filtre d'affichage :
# tout titre ingéré reste accessible par recherche directe du ticker.
MIN_MARKET_CAP = 50e6
MIN_DOLLAR_VOLUME_3M = 300e3
DISPLAY_FLOOR = 300e6          # seuil par défaut du screener, abaissable d'un clic
MAX_FILING_AGE_DAYS = 183


class RecentFilings:
    """
    Date et forme du dernier dépôt périodique, par CIK, lue dans les index trimestriels.

    Le fichier est à largeur fixe et non en CSV : les noms de sociétés contiennent des
    virgules et des espaces. Les colonnes sont donc découpées par position.
    """

    def __init__(self):
        self.last: dict = {}

    @staticmethod
    def _quarters(back_days: int, today: Optional[date] = None):
        today = today or date.today()
        start = today - timedelta(days=back_days)
        out, y, q = [], start.year, (start.month - 1) // 3 + 1
        while (y, q) <= (today.year, (today.month - 1) // 3 + 1):
            out.append((y, q))
            y, q = (y + 1, 1) if q == 4 else (y, q + 1)
        return out

    def load(self, provider, back_days: int = MAX_FILING_AGE_DAYS,
             cache_dir: Optional[str] = None) -> "RecentFilings":
        for year, q in self._quarters(back_days):
            url = INDEX_URL.format(year=year, q=q)
            path = os.path.join(cache_dir, f"form_{year}Q{q}.idx") if cache_dir else None
            if path and os.path.exists(path):
                raw = open(path, "rb").read()
            else:
                try:
                    raw = provider._get(url).content
                except FetchError:
                    log.warning("index %sQ%s indisponible, ignoré", year, q)
                    continue
                if path:
                    os.makedirs(cache_dir, exist_ok=True)
                    open(path, "wb").write(raw)
            self._parse(raw)
        log.info("%d CIK avec un dépôt périodique récent", len(self.last))
        return self

    # Le fichier n'est pas exploitable par positions fixes : la ligne d'en-tête et les
    # lignes de données n'ont pas les mêmes largeurs de colonnes. Coder les positions en
    # dur tronquait la date en « 2026-07 » — et la comparaison lexicographique restait à
    # peu près juste, donc l'erreur ne se voyait pas.
    # On ancre donc sur la queue de ligne, qui est sans ambiguïté :
    #     ... <CIK> <AAAA-MM-JJ> edgar/data/...
    TAIL = re.compile(r"\s(\d{1,10})\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$")

    def _parse(self, raw: bytes) -> None:
        for line in raw.decode("latin-1").splitlines():
            m = self.TAIL.search(line)
            if m is None:
                continue
            form = line.split("  ", 1)[0].strip()
            if form not in PERIODIC and form not in ANNUAL_ONLY:
                continue
            cik, filed = int(m.group(1)), m.group(2)
            prev = self.last.get(cik)
            if prev is None or filed > prev[0]:
                self.last[cik] = (filed, form)

    def is_annual_only(self, cik: int) -> bool:
        rec = self.last.get(cik)
        return bool(rec and rec[1] in ANNUAL_ONLY)

    def filed_at(self, cik: int) -> Optional[date]:
        rec = self.last.get(cik)
        return date.fromisoformat(rec[0]) if rec else None


def is_common_stock_ticker(ticker: str) -> bool:
    """
    Le fichier SEC ne porte aucun champ de type de titre : le suffixe est le seul
    signal disponible à ce stade. Écarte 1 250 lignes sur 7 707.
      -P*  preferred        -W / -WT  warrants       -U / -UN  unités de SPAC
      XXXXU / XXXXW / XXXXR  mêmes catégories, convention Nasdaq à 5 lettres
    Le reste des coquilles part au filtre de dépôt récent.
    """
    if not ticker:
        return False
    if "-" in ticker:
        suffix = ticker.split("-", 1)[1]
        if suffix[:1] in ("P", "W", "U", "R"):
            return False
    if len(ticker) == 5 and ticker[-1] in ("U", "W", "R") and ticker[:4].isalpha():
        return False
    return True


def build_candidates(provider, filings: RecentFilings,
                     max_age_days: int = MAX_FILING_AGE_DAYS,
                     today: Optional[date] = None) -> tuple:
    """
    Passe A. Retourne (candidats, entonnoir) — l'entonnoir sert au journal : une chute
    brutale d'une étape à l'autre signale une source cassée, pas un univers rétréci.
    """
    today = today or date.today()
    cutoff = (today - timedelta(days=max_age_days)).isoformat()
    funnel, out = {}, []

    issuers = list(provider.list_issuers())
    funnel["places US retenues"] = len(issuers)

    issuers = [i for i in issuers if is_common_stock_ticker(i.ticker)]
    funnel["hors preferred/units/warrants"] = len(issuers)

    issuers = [i for i in issuers if i.cik in filings.last]
    funnel["dépôt périodique trouvé"] = len(issuers)

    issuers = [i for i in issuers if filings.last[i.cik][0] >= cutoff]
    funnel[f"dépôt < {max_age_days} jours"] = len(issuers)

    for i in issuers:
        if filings.is_annual_only(i.cik):
            continue
        out.append(i)
    funnel["hors déposants annuels 20-F/40-F"] = len(out)

    return out, funnel


def enrich_with_sic(provider, issuers, run=None, limit: Optional[int] = None) -> int:
    """
    Le SIC décide du `mapping_profile`, et il n'existe ni dans la liste des émetteurs ni
    dans les index trimestriels : seul l'endpoint `submissions` le porte, un appel par
    société. C'est le seul endroit de la passe A qui coûte cher — mesuré à ~9 min pour
    4 800 candidats à 9 req/s. Job mensuel : acceptable.

    Renseigne issuer.sic sur place. Un échec ne fait pas tomber le lot : le profil
    retombe sur `standard`, ce qui est le comportement correct par défaut.
    """
    from providers.base import ProviderError
    done = 0
    for i in issuers[:limit] if limit else issuers:
        try:
            sic = provider.get_sic(i.cik)
        except ProviderError as e:
            if run:
                run.fail_provider(e, ticker=i.ticker, cik=i.cik)
            continue
        if sic:
            object.__setattr__(i, "sic", sic)
            done += 1
    return done


# =============================================================================
# Passe B — SQL pur, aucun appel réseau
# =============================================================================
FINALIZE_SQL = """
with fenetre as (
    select company_id,
           percentile_cont(0.5) within group (order by close_adj * volume) as dv_median,
           count(*) as n_seances
    from prices_daily
    where dt >= current_date - interval '3 months'
    group by company_id
)
update companies c set
    median_dollar_volume_3m = f.dv_median,
    -- coalesce indispensable : une capitalisation absente rend la comparaison NULL,
    -- et in_universe est NOT NULL. Donnée manquante => hors univers, jamais NULL.
    in_universe = coalesce(
        c.market_cap >= %(min_cap)s
        and f.dv_median >= %(min_dv)s
        and f.n_seances >= 30
        and c.mapping_profile in ('standard','financial','reit'),
        false
    ),
    history_years = case when coalesce(c.market_cap, 0) >= %(display_floor)s
                         then 10 else 5 end,
    updated_at = now()
from fenetre f
where f.company_id = c.company_id
"""


def finalize_universe(cn, min_cap: float = MIN_MARKET_CAP,
                      min_dv: float = MIN_DOLLAR_VOLUME_3M,
                      display_floor: float = DISPLAY_FLOOR) -> dict:
    """
    Passe B. Applique capitalisation et volume médian, puis fixe la profondeur
    d'historique visée. `n_seances >= 30` écarte les titres à peine cotés, dont la
    médiane sur trois séances ne veut rien dire.
    """
    with cn.cursor() as c:
        c.execute(FINALIZE_SQL, {"min_cap": min_cap, "min_dv": min_dv,
                                 "display_floor": display_floor})
        touched = c.rowcount
        c.execute("""
            select count(*) filter (where in_universe)                                as retenus,
                   count(*) filter (where in_universe and market_cap >= %s)           as au_dessus_seuil,
                   count(*) filter (where not in_universe and market_cap < %s)        as hors_capi,
                   count(*) filter (where not in_universe
                                    and median_dollar_volume_3m < %s)                 as hors_volume,
                   count(*) filter (where in_universe and data_quality_score < 0.5)   as qualite_faible
            from companies
        """, (display_floor, min_cap, min_dv))
        k = ("retenus", "au_dessus_seuil", "hors_capi", "hors_volume", "qualite_faible")
        stats = dict(zip(k, c.fetchone()))
    stats["lignes_evaluees"] = touched
    return stats
