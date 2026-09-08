"""
db.py — Persistance. Seul module qui connaît psycopg2 et le schéma SQL.

Porte les trois garanties du plan de phase 1 :
  * idempotence   — relancer un script ne duplique ni ne perd rien
  * retraitement  — un dépôt plus récent écrase, un dépôt plus ancien est ignoré
  * journal       — quel ticker a échoué, à quelle étape, et pourquoi
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from typing import Iterable, Optional

import psycopg2
import psycopg2.extras as X

import mapping as M
from providers.base import (FundamentalsResult, PriceSeries, ProviderError,
                            database_url)

log = logging.getLogger(__name__)

# Colonnes numériques de fundamentals, dans l'ordre du schéma.
FUND_COLS = [n for n in M.STORED if not n.startswith("_")]


def connect(url: Optional[str] = None):
    cn = psycopg2.connect(url or database_url())
    cn.autocommit = False
    return cn


def _clean(v):
    """Postgres refuse NaN et Infinity en double precision : on les traite en absence."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or abs(f) > 1e300:
        return None
    return f


# =============================================================================
# Journal d'ingestion
# =============================================================================
class Run:
    """Un passage d'un job. Ouvre une ligne ingestion_runs, la ferme à la sortie."""

    def __init__(self, cn, job: str, source_stamp: str = ""):
        self.cn, self.job = cn, job
        self.n_ok = self.n_failed = 0
        with cn.cursor() as c:
            c.execute("insert into ingestion_runs (job, source_stamp) values (%s,%s) "
                      "returning run_id", (job, source_stamp))
            self.run_id = c.fetchone()[0]
        cn.commit()

    def fail(self, *, ticker=None, cik=None, stage="unknown", reason="", detail=""):
        self.n_failed += 1
        with self.cn.cursor() as c:
            c.execute("insert into ingestion_errors (run_id,ticker,cik,stage,reason,detail) "
                      "values (%s,%s,%s,%s,%s,%s)",
                      (self.run_id, ticker, cik, stage, reason[:200], (detail or "")[:1000]))

    def fail_provider(self, exc: ProviderError, *, ticker=None, cik=None):
        self.fail(ticker=ticker, cik=cik, stage=getattr(exc, "stage", "unknown"),
                  reason=type(exc).__name__, detail=str(exc))

    def ok(self, n: int = 1):
        self.n_ok += n

    def close(self, notes: str = ""):
        with self.cn.cursor() as c:
            c.execute("update ingestion_runs set finished_at=now(), n_ok=%s, n_failed=%s, "
                      "notes=%s where run_id=%s",
                      (self.n_ok, self.n_failed, notes or None, self.run_id))
        self.cn.commit()
        log.info("[%s] run %s : %d ok, %d en échec", self.job, self.run_id,
                 self.n_ok, self.n_failed)


@contextmanager
def run(cn, job: str, source_stamp: str = ""):
    r = Run(cn, job, source_stamp)
    try:
        yield r
    finally:
        r.close()


# =============================================================================
# companies
# =============================================================================
def upsert_companies(cn, issuers, profiles: dict = None) -> dict:
    """Insère ou met à jour les lignes de cotation. Retourne {ticker: company_id}."""
    profiles = profiles or {}
    rows = [(i.cik, i.ticker, i.name[:200], i.exchange,
             profiles.get(i.ticker, "standard")) for i in issuers]
    with cn.cursor() as c:
        X.execute_values(c, """
            insert into companies (cik, ticker, name, exchange, mapping_profile) values %s
            on conflict (ticker) do update
              set cik = excluded.cik, name = excluded.name,
                  exchange = excluded.exchange,
                  mapping_profile = excluded.mapping_profile,
                  updated_at = now()
        """, rows, page_size=1000)
        c.execute("select ticker, company_id from companies")
        return dict(c.fetchall())


def update_company_facts(cn, res: FundamentalsResult, sic: Optional[int] = None) -> None:
    with cn.cursor() as c:
        c.execute("""
            update companies set
              sic = coalesce(%s, sic),
              mapping_profile = %s,
              shares_outstanding = %s,
              shares_as_of = %s,
              last_xbrl_filing = %s,
              data_quality_score = %s,
              fiscal_years_available = %s,
              updated_at = now()
            where ticker = %s
        """, (sic, res.mapping_profile, _clean(res.shares_outstanding), res.shares_as_of,
              res.last_filing, res.data_quality_score, res.fiscal_years, res.ticker))


def refresh_market_caps(cn) -> int:
    """
    Capitalisation = actions EDGAR × dernier cours. Jamais lue chez un fournisseur de
    cours : c'est le seul chemin traçable jusqu'au dépôt.
    Marque incertaines les lignes multi-classes, où companyfacts, qui supprime les
    dimensions, peut n'avoir renvoyé qu'une seule classe.
    """
    with cn.cursor() as c:
        c.execute("""
            with dernier as (
              select distinct on (company_id) company_id, dt, close_adj
              from prices_daily order by company_id, dt desc
            ),
            multi as (select cik from companies group by cik having count(*) > 1)
            update companies c set
              market_cap = c.shares_outstanding * d.close_adj,
              market_cap_as_of = d.dt,
              market_cap_uncertain = (c.cik in (select cik from multi)),
              updated_at = now()
            from dernier d
            where d.company_id = c.company_id and c.shares_outstanding is not null
        """)
        return c.rowcount


# =============================================================================
# fundamentals — la clause qui règle idempotence et retraitement d'un coup
# =============================================================================
_SET = ", ".join(f"{c} = excluded.{c}" for c in FUND_COLS)

UPSERT_FUNDAMENTALS = f"""
insert into fundamentals
  (cik, period_end, fiscal_period, {', '.join(FUND_COLS)},
   accn, filed, inferred_zero, q4_derived)
values %s
on conflict (cik, period_end, fiscal_period) do update set
  {_SET},
  accn = excluded.accn,
  filed = excluded.filed,
  inferred_zero = excluded.inferred_zero,
  q4_derived = excluded.q4_derived,
  ingested_at = now()
where excluded.filed is not null
  and (fundamentals.filed is null or excluded.filed > fundamentals.filed)
"""


def upsert_fundamentals(cn, rows: Iterable) -> int:
    payload = [
        tuple([r.cik, r.period_end, r.fiscal_period]
              + [_clean(r.values.get(col)) for col in FUND_COLS]
              + [r.accn, r.filed, list(r.inferred_zero or []), bool(r.q4_derived)])
        for r in rows
    ]
    if not payload:
        return 0
    with cn.cursor() as c:
        X.execute_values(c, UPSERT_FUNDAMENTALS, payload, page_size=500)
    return len(payload)


# =============================================================================
# prices — un appel de fonction par mois touché, fusion idempotente côté base
# =============================================================================
def upsert_prices(cn, company_id: int, series: PriceSeries) -> int:
    """
    Découpe la série en mois et appelle merge_price_month(), qui fusionne sans écraser
    le reste du mois. Rejouer le même lot laisse la base identique.
    """
    if not series.ok or not series.days:
        return 0
    months: dict = {}
    for d, close, vol in series.days:
        months.setdefault((d.year, d.month), []).append((d.day, close, vol))
    with cn.cursor() as c:
        for (y, m), vals in months.items():
            vals.sort()
            c.execute(
                "select merge_price_month(%s::smallint, %s::date, %s::smallint[], "
                "%s::real[], %s::real[])",
                (company_id, date(y, m, 1),
                 [int(v[0]) for v in vals],
                 [float(v[1]) for v in vals],
                 [float(v[2]) for v in vals]),
            )
    return len(months)


def prune_price_history(cn, company_id: int, years: int) -> int:
    """Applique la profondeur d'historique visée. Le schéma ne plafonne rien lui-même."""
    with cn.cursor() as c:
        c.execute("delete from prices where company_id = %s "
                  "and ym < (current_date - make_interval(years => %s))",
                  (company_id, years))
        return c.rowcount


# =============================================================================
# Sélection et état de fraîcheur
# =============================================================================
MAPPABLE = ("standard", "financial", "reit")


def select_universe(cn, in_universe: bool = True, stale_first: Optional[str] = None,
                    limit: Optional[int] = None, mappable_only: bool = True) -> list:
    """
    Retourne [(company_id, cik, ticker, sic, mapping_profile, history_years)].

    `stale_first` prend 'fundamentals' ou 'prices' : le tri place en tête les sociétés
    jamais traitées puis les plus anciennes. C'est ce qui rend un lot interrompu
    reprenable sans état externe — le curseur est la donnée elle-même.
    """
    where = ["true"]
    if in_universe:
        where.append("in_universe")
    if mappable_only:
        where.append("mapping_profile in %(mappable)s")
    order = ""
    if stale_first in ("fundamentals", "prices"):
        order = f"order by {stale_first}_refreshed_at asc nulls first, company_id"
    sql = (f"select company_id, cik, ticker, sic, mapping_profile::text, history_years "
           f"from companies where {' and '.join(where)} {order} "
           f"{'limit %(limit)s' if limit else ''}")
    with cn.cursor() as c:
        c.execute(sql, {"mappable": MAPPABLE, "limit": limit})
        return c.fetchall()


def mark_out_of_scope(cn, ticker: str, profile: str) -> None:
    """
    Persiste le profil hors périmètre remonté par out_of_scope(). Sans ça, une société
    IFRS reste marquée 'standard' et n'est écartée de l'univers que par accident
    d'absence de capitalisation — un filtre juste pour une mauvaise raison.
    """
    with cn.cursor() as c:
        c.execute("update companies set mapping_profile = %s::mapping_profile_t, "
                  "in_universe = false, updated_at = now() where ticker = %s",
                  (profile, ticker))


def mark_refreshed(cn, kind: str, ids: Iterable, on: Optional[date] = None) -> int:
    """kind = 'fundamentals' (clé cik) ou 'prices' (clé company_id)."""
    col = {"fundamentals": ("fundamentals_refreshed_at", "cik"),
           "prices": ("prices_refreshed_at", "company_id")}[kind]
    ids = list(ids)
    if not ids:
        return 0
    with cn.cursor() as c:
        c.execute(f"update companies set {col[0]} = %s where {col[1]} = any(%s)",
                  (on or date.today(), ids))
        return c.rowcount


def price_coverage(cn) -> dict:
    """{company_id: (premier_mois, dernier_mois)} — base du backfill différencié."""
    with cn.cursor() as c:
        c.execute("select company_id, min(ym), max(ym) from prices group by company_id")
        return {r[0]: (r[1], r[2]) for r in c.fetchall()}
