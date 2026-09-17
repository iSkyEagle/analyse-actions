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

# Nombre de mois accumulés avant écriture. 5 000 tient largement dans une instruction
# et limite le nombre d'allers-retours sans faire enfler la table temporaire.
FLUSH_MOIS = 5000


def _flush(cn, tampon, run) -> int:
    if not tampon:
        return 0
    try:
        n = db.upsert_prices_bulk(cn, tampon)
        cn.commit()
        return n
    except Exception as e:                      # un paquet perdu n'arrête pas le job
        cn.rollback()
        run.fail(stage="upsert", reason=type(e).__name__, detail=str(e))
        return 0


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


def run(mode: str, limit: int | None, window_days: int, dry_run: bool,
        scope: str = "universe") -> int:
    today = date.today()
    cn = db.connect()
    provider = YFinanceProvider()

    # `in_universe` est fixé par finalize_universe, qui a besoin des cours : au premier
    # démarrage il est faux partout, et filtrer dessus ne renverrait rien. On bascule
    # alors sur l'ensemble des candidats mappables.
    # Le scope 'candidats' sert aussi hors amorçage : sans cours, une société sortie de
    # l'univers ne pourrait jamais y rentrer, même si sa capitalisation remonte. À
    # lancer une fois par mois, avant `ingest_universe --finalize-only`.
    rows = db.select_universe(cn, in_universe=(scope == "universe"),
                              stale_first="prices", limit=limit)
    if not rows and scope == "universe":
        log.warning("univers non encore finalisé — bascule sur l'ensemble des candidats")
        scope = "candidates"
        rows = db.select_universe(cn, in_universe=False, stale_first="prices", limit=limit)
    if not rows:
        log.warning("aucun candidat en base — lancer d'abord ingest_universe")
        return 0
    log.info("scope %s : %d sociétés", scope, len(rows))

    if mode == "daily":
        # Une fenêtre fixe de 7 jours laisse un trou dès que le job saute plus d'une
        # semaine — trou que le backfill, qui n'étend que vers l'amont, ne comble jamais.
        # La fenêtre part donc de la séance la plus ancienne parmi les dernières connues,
        # bornée à 90 jours, en un seul appel groupé.
        coverage = db.price_coverage(cn)
        derniers = [coverage[r[0]][1] for r in rows if r[0] in coverage]
        plus_ancien = min(derniers) if derniers else today
        start = max(min(plus_ancien, today - timedelta(days=window_days)),
                    today - timedelta(days=90))
        if start < today - timedelta(days=window_days):
            log.info("rattrapage : la fenêtre remonte au %s", start)
        plan = {r[2]: (r[0], start, today) for r in rows}
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
            log.info("fenêtre %s -> %s : %d sociétés, par lots de %d",
                     start, end, len(tickers), provider.max_batch)
            # Les mois sont accumulés puis écrits par paquets. Écrire société par
            # société faisait un appel de fonction par mois — ~270 000 sur un backfill
            # complet, de quoi épuiser les crédits CPU d'une instance gratuite.
            tampon, en_attente = [], []
            for series in provider.get_price_history(tickers, start, end):
                if not series.ok:
                    r.fail(ticker=series.ticker, stage="fetch", reason=series.reason)
                    continue
                tampon.extend(db._months_of(plan[series.ticker][0], series))
                en_attente.append(plan[series.ticker][0])
                r.ok()
                done += 1
                if len(tampon) >= FLUSH_MOIS:
                    n = _flush(cn, tampon, r)
                    months += n
                    if n:                       # marqué rafraîchi seulement si écrit
                        refreshed.extend(en_attente)
                    tampon, en_attente = [], []
            n = _flush(cn, tampon, r)
            months += n
            if n:
                refreshed.extend(en_attente)
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
    p.add_argument("--scope", choices=("universe", "candidates"), default="universe",
                   help="'candidates' ignore in_universe : amorçage, et réévaluation mensuelle")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()

    settings.setup_logging(a.verbose)
    settings.require_env()
    run(a.mode, a.limit, a.window_days, a.dry_run, a.scope)


if __name__ == "__main__":
    main()
