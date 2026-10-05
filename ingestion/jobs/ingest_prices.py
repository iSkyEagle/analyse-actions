"""
jobs/ingest_prices.py — Ingestion des cours.

Deux modes, une seule mécanique de fusion.

  --mode daily     rattrape les derniers jours pour tout l'univers. Quelques minutes.
                   La fenêtre déborde volontairement (7 jours par défaut) : week-ends,
                   jours fériés et corrections rétroactives d'ajustement sont ainsi
                   repris sans traitement particulier, la fusion étant idempotente.

  --mode backfill  reconstruit l'historique sur `history_years`, en le remplaçant.
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


def _ecrire(cn, lot: list, run) -> tuple:
    """
    Écrit un lot de séries COMPLÈTES : (company_id, remplacer, mois, divisions, dividendes).

    Une série n'est jamais coupée entre deux lots : remplacer l'historique d'une
    société en deux fois effacerait la première moitié.

    Renvoie (mois écrits, sociétés écrites, sociétés à reconstruire).
    """
    if not lot:
        return 0, [], set()
    remplaces = [cid for cid, rem, mois, _, _ in lot if rem and mois]
    try:
        nouvelles = db.ecrire_evenements(
            cn,
            [(cid, d, x) for cid, _, _, divs, _ in lot for d, x in divs],
            [(cid, d, x) for cid, _, _, _, dvds in lot for d, x in dvds],
            remplaces,
        )
        a_remplacer = [m for cid, rem, mois, _, _ in lot if rem for m in mois]
        # Une société dont une division vient d'apparaître n'est PAS fusionnée : ses
        # nouveaux cours sont en unités post-division, son historique stocké non.
        a_fusionner = [m for cid, rem, mois, _, _ in lot if not rem and cid not in nouvelles for m in mois]
        n = 0
        if a_remplacer:
            n += db.remplacer_cours(cn, remplaces, a_remplacer)
        if a_fusionner:
            n += db.upsert_prices_bulk(cn, a_fusionner)
        cn.commit()
    except Exception as e:                      # un paquet perdu n'arrête pas le job
        cn.rollback()
        run.fail(stage="ecriture", reason=type(e).__name__, detail=str(e))
        return 0, [], set()
    ecrites = [cid for cid, rem, mois, _, _ in lot if mois and (rem or cid not in nouvelles)]
    return n, ecrites, nouvelles


def _target_start(history_years: int, today: date) -> date:
    return today.replace(year=today.year - history_years, day=1)


def plan_backfill(cn, rows, today: date) -> dict:
    """
    Sociétés à reconstruire, et leur fenêtre : toute la profondeur visée.

    Une société déjà reconstruite sort du plan d'elle-même : le curseur est la donnée,
    ce qui rend le backfill reprenable et borné.
    """
    ids = [r[0] for r in rows]
    coverage = db.price_coverage(cn, ids)
    reconstruire = db.a_reconstruire(cn, ids)
    plan = {}
    for company_id, cik, ticker, sic, profile, history_years in rows:
        target = _target_start(history_years, today)
        have = coverage.get(company_id)
        # Reconstruction : historique encore ajusté des dividendes, nouvelle division, ou
        # profondeur visée relevée. Toute la profondeur visée est téléchargée et
        # l'historique stocké REMPLACÉ : ce qui revient est, par construction, tout ce
        # qui existe. Une société reconstruite est donc complète, quel que soit son âge.
        #
        # Il n'y a plus de mode « plage manquante ». Il jugeait incomplète toute société
        # introduite depuis moins de dix ans — son historique commence forcément après
        # la profondeur visée — et la retéléchargeait chaque nuit, sans fin : le
        # compteur de sociétés à compléter ne pouvait jamais atteindre zéro.
        if company_id in reconstruire or have is None:
            plan[ticker] = (company_id, target, today, True)
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
    # Backfill : on examine TOUTE la population, et c'est le plan qui est borné — pas la
    # sélection. La version précédente prenait les `limit` sociétés aux cours les moins
    # récemment mis à jour, puis cherchait lesquelles avaient un historique incomplet.
    # Or le job quotidien remet cette date à jour pour tout l'univers chaque soir : à
    # 5 h, toutes étaient à égalité, départagées par leur identifiant, et c'étaient
    # toujours les 500 mêmes qui étaient choisies. Une fois celles-ci complètes, le
    # backfill ne trouvait plus rien à faire et les ~2 400 autres n'atteignaient jamais
    # leur profondeur visée. Le bon critère est l'historique manquant, pas la fraîcheur.
    db.assurer_indices(cn)
    cn.commit()
    limite_selection = None if mode == "backfill" else limit
    rows = db.select_universe(cn, in_universe=(scope == "universe"),
                              stale_first="prices", limit=limite_selection)
    if not rows and scope == "universe":
        log.warning("univers non encore finalisé — bascule sur l'ensemble des candidats")
        scope = "candidates"
        rows = db.select_universe(cn, in_universe=False, stale_first="prices", limit=limite_selection)
    if not rows:
        log.warning("aucun candidat en base — lancer d'abord ingest_universe")
        return 0
    if scope == "universe":
        rows = rows + db.lignes_indices(cn)      # ^GSPC et ^SP500TR, hors univers par nature
    log.info("scope %s : %d séries", scope, len(rows))

    if mode == "daily":
        # Une fenêtre fixe de 7 jours laisse un trou dès que le job saute plus d'une
        # semaine — trou que le backfill, qui n'étend que vers l'amont, ne comble jamais.
        # La fenêtre part donc de la séance la plus ancienne parmi les dernières connues,
        # bornée à 90 jours, en un seul appel groupé.
        coverage = db.price_coverage(cn, [r[0] for r in rows])
        derniers = [coverage[r[0]][1] for r in rows if r[0] in coverage]
        plus_ancien = min(derniers) if derniers else today
        start = max(min(plus_ancien, today - timedelta(days=window_days)),
                    today - timedelta(days=90))
        if start < today - timedelta(days=window_days):
            log.info("rattrapage : la fenêtre remonte au %s", start)
        plan = {r[2]: (r[0], start, today, False) for r in rows}
    else:
        complet = plan_backfill(cn, rows, today)
        # Ordre déterministe par identifiant : les identifiants suivent l'ordre du fichier
        # SEC à la création, trié par capitalisation décroissante, donc les grandes
        # valeurs passent en premier. Une société complétée sort d'elle-même du plan :
        # le curseur est la donnée, aucun état à conserver entre deux passages.
        # Reconstructions d'abord, puis par identifiant : les grandes valeurs en premier.
        ordre = sorted(complet.items(), key=lambda kv: (not kv[1][3], kv[1][0]))
        plan = dict(ordre[:limit] if limit else ordre)
        n_rec = sum(1 for v in complet.values() if v[3])
        log.info("backfill : %d sociétés à traiter sur %d examinées, dont %d à reconstruire ; "
                 "%d traitées ce passage", len(complet), len(rows), n_rec, len(plan))

    if not plan:
        log.info("rien à faire")
        return 0
    if dry_run:
        for t, (_, s, e, rem) in list(plan.items())[:10]:
            log.info("  %-8s %s -> %s%s", t, s, e, "  (reconstruction)" if rem else "")
        log.info("... %d sociétés au total (dry-run)", len(plan))
        return 0

    # Les fenêtres diffèrent d'une société à l'autre en backfill. On regroupe par
    # fenêtre identique pour préserver l'appel groupé : un lot par fenêtre, jamais
    # un appel par ticker.
    groups: dict = {}
    for ticker, (company_id, start, end, remplacer) in plan.items():
        groups.setdefault((start, end), []).append(ticker)

    done, months, refreshed, a_refaire = 0, 0, [], set()
    with db.run(cn, f"prices_{mode}", provider.name) as r:
        for (start, end), tickers in groups.items():
            log.info("fenêtre %s -> %s : %d séries, par lots de %d",
                     start, end, len(tickers), provider.max_batch)
            lot, n_mois = [], 0
            for series in provider.get_price_history(tickers, start, end):
                if not series.ok:
                    r.fail(ticker=series.ticker, stage="fetch", reason=series.reason)
                    continue
                cid, _, _, remplacer = plan[series.ticker]
                mois = db._months_of(cid, series)
                lot.append((cid, remplacer, mois, series.splits, series.dividends))
                n_mois += len(mois)
                r.ok()
                done += 1
                if n_mois >= FLUSH_MOIS:
                    n, ecrites, nouvelles = _ecrire(cn, lot, r)
                    months, refreshed, a_refaire = months + n, refreshed + ecrites, a_refaire | nouvelles
                    lot, n_mois = [], 0
            n, ecrites, nouvelles = _ecrire(cn, lot, r)
            months, refreshed, a_refaire = months + n, refreshed + ecrites, a_refaire | nouvelles
        if a_refaire:
            log.warning("division détectée chez %d société(s) : cours non fusionnés, historique "
                        "à reconstruire au prochain backfill", len(a_refaire))
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
