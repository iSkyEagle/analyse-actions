"""
jobs/ingest_prices.py — Ingestion des cours.

Deux modes, une seule mécanique de fusion.

  --mode daily     rattrape les derniers jours pour tout l'univers. Quelques minutes.
                   La fenêtre déborde volontairement (7 jours par défaut) : week-ends,
                   jours fériés et corrections rétroactives d'ajustement sont ainsi
                   repris sans traitement particulier, la fusion étant idempotente.

  --mode backfill  complète l'historique jusqu'à `history_years`, société par société.
                   Progressif : --limit permet d'étaler la reprise sur plusieurs
                   passages sans jamais tout retélécharger.

Le stockage ne plafonne rien : `history_years` n'est qu'une profondeur visée, fixée à
10 ans au-dessus de 300 M$ et 5 ans en dessous. 5 et non 3, faute de quoi la médiane
5 ans des multiples serait structurellement absente des petites capitalisations.

Usage :
    python -m jobs.ingest_prices --mode daily
    python -m jobs.ingest_prices --mode backfill --limit 500
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import db
import settings
from providers.base import PriceSeries
from providers.prices import YFinanceProvider

log = logging.getLogger("ingest.prices")


def _target_start(history_years: int, today: date) -> date:
    return today.replace(year=today.year - history_years, day=1)


def plan_backfill(cn, rows, today: date) -> dict:
    """
    Détermine, par société, la fenêtre manquante en amont de l'historique existant.

    Les sociétés déjà couvertes sont écartées du plan : c'est ce qui rend le backfill
    reprenable et borné, au lieu de retélécharger l'univers à chaque passage.
    """
    coverage = db.price_coverage(cn)
    plan = {}
    for company_id, cik, ticker, sic, profile, history_years in rows:
        target = _target_start(history_years, today)
        have = coverage.get(company_id)
        if have is None:
            plan[ticker] = (company_id, target, today)
        elif have[0] > target:
            # marge d'un mois pour recouvrir la frontière et éviter un trou
            plan[ticker] = (company_id, target, have[0] + timedelta(days=31))
    return plan


def run(mode: str, limit: int | None, window_days: int, dry_run: bool) -> int:
    today = date.today()
    cn = db.connect()
    provider = YFinanceProvider()

    rows = db.select_universe(cn, stale_first="prices", limit=limit)
    if not rows:
        log.warning("aucune société dans l'univers — lancer d'abord ingest_universe")
        return 0

    if mode == "daily":
        plan = {r[2]: (r[0], today - timedelta(days=window_days), today) for r in rows}
    else:
        plan = plan_backfill(cn, rows, today)
        log.info("backfill : %d sociétés à compléter sur %d examinées", len(plan), len(rows))

    if not plan:
        log.info("rien à faire")
        return 0
    if dry_run:
        for t, (_, s, e) in list(plan.items())[:10]:
            log.info("  %-8s %s -> %s", t, s, e)
        log.info("... %d sociétés au total (dry-run)", len(plan))
        return 0

    # Les fenêtres diffèrent d'une société à l'autre en backfill. On regroupe par
    # fenêtre identique pour préserver l'appel groupé : un lot par fenêtre, jamais
    # un appel par ticker.
    groups: dict = {}
    for ticker, (company_id, start, end) in plan.items():
        groups.setdefault((start, end), []).append(ticker)

    done, months, refreshed = 0, 0, []
    with db.run(cn, f"prices_{mode}", provider.name) as r:
        for (start, end), tickers in groups.items():
            log.info("lot de %d tickers, %s -> %s", len(tickers), start, end)
            for series in provider.get_price_history(tickers, start, end):
                if not series.ok:
                    r.fail(ticker=series.ticker, stage="fetch", reason=series.reason)
                    continue
                company_id = plan[series.ticker][0]
                try:
                    months += db.upsert_prices(cn, company_id, series)
                except Exception as e:                      # une société ne tue pas le lot
                    cn.rollback()
                    r.fail(ticker=series.ticker, stage="upsert",
                           reason=type(e).__name__, detail=str(e))
                    continue
                refreshed.append(company_id)
                r.ok()
                done += 1
            cn.commit()
        db.mark_refreshed(cn, "prices", refreshed)
        n = db.refresh_market_caps(cn)
        cn.commit()
        log.info("%d séries, %d mois écrits, %d capitalisations recalculées", done, months, n)

    cn.close()
    return done


def main() -> None:
    p = argparse.ArgumentParser(description="Ingestion des cours")
    p.add_argument("--mode", choices=("daily", "backfill"), default="daily")
    p.add_argument("--limit", type=int, default=None,
                   help="nombre de sociétés à traiter (les plus anciennes d'abord)")
    p.add_argument("--window-days", type=int, default=7,
                   help="profondeur du rattrapage quotidien")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()

    settings.setup_logging(a.verbose)
    settings.require_env()
    run(a.mode, a.limit, a.window_days, a.dry_run)


if __name__ == "__main__":
    main()
