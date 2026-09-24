"""
jobs/ingest_metrics.py — Calcul des métriques pour l'univers.

Lit les comptes et les cours déjà en base, calcule avec metriques.py, écrit dans
`metrics` et `metric_series`. Aucun appel réseau : le job ne dépend que de la base.

Par lots de 200 sociétés : quatre requêtes par lot — comptes, cours, capitalisations,
écriture — au lieu de quatre par société. Sur le pooler, c'est la différence entre
quelques secondes et plusieurs minutes.

Robustesse :
  - une société qui fait échouer le calcul est journalisée, le lot continue ;
  - un lot qui fait échouer l'écriture est annulé en entier, les lots suivants
    continuent, et les sociétés concernées gardent leur dernier instantané ;
  - relancer le job laisse la base identique.

Usage :
    python -m jobs.ingest_metrics              # quotidien : treize mois de cours
    python -m jobs.ingest_metrics --complet    # hebdomadaire : cinq ans, tout recalculé
    python -m jobs.ingest_metrics --tickers MSFT,KO
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, timedelta

import db
import metriques as MQ
import settings

log = logging.getLogger("ingest.metrics")
LOT = 200
PROFONDEUR_COMPLETE = timedelta(days=5 * 366 + 15)    # pire baisse sur 5 ans, avec une marge
PROFONDEUR_QUOTIDIENNE = timedelta(days=400)          # volatilité et plus haut sur 52 semaines


def run(tickers: list | None = None, complet: bool = False) -> int:
    """
    `complet` : lit cinq ans de cours et recalcule tout — passage hebdomadaire.
    Sinon, treize mois de cours, et les métriques de HISTORIQUE_LONG reprennent leur
    dernière valeur connue au lieu d'être recalculées sur une fenêtre trop courte.
    """
    cn = db.connect()
    aujourdhui = date.today()
    depuis = aujourdhui - (PROFONDEUR_COMPLETE if complet else PROFONDEUR_QUOTIDIENNE)

    if tickers:
        with cn.cursor() as c:
            c.execute("select company_id, cik, ticker, sic, mapping_profile::text, history_years "
                      "from companies where ticker = any(%s)", (tickers,))
            societes = c.fetchall()
    else:
        societes = db.select_universe(cn)
    log.info("%d sociétés à calculer", len(societes))

    ok = 0
    debut = time.monotonic()
    with db.run(cn, "metrics", "base") as r:
        for i in range(0, len(societes), LOT):
            lot = societes[i:i + LOT]
            ids = [s[0] for s in lot]
            try:
                comptes = db.charger_fondamentaux(cn, [s[1] for s in lot])
                cours = db.charger_cours(cn, ids, depuis)
                with cn.cursor() as c:
                    c.execute("select company_id, market_cap from companies where company_id = any(%s)", (ids,))
                    capis = dict(c.fetchall())
                    conserves: dict = {}
                    if not complet:
                        c.execute("select company_id, metric_id, value, raison from metrics "
                                  "where company_id = any(%s) and metric_id = any(%s)",
                                  (ids, list(MQ.HISTORIQUE_LONG)))
                        for cid, mid, v, rs in c.fetchall():
                            conserves.setdefault(cid, {})[mid] = (v, rs)
            except Exception as e:
                cn.rollback()
                for s in lot:
                    r.fail(ticker=s[2], cik=s[1], stage="lecture", reason=type(e).__name__, detail=str(e))
                continue

            lignes, series, calculees = [], [], []
            for company_id, cik, ticker, sic, profil, _ in lot:
                lignes_cik = comptes.get(cik, [])
                exercices = [x for x in lignes_cik if x["fiscal_period"] == "FY"]
                trimestres = [x for x in lignes_cik if x["fiscal_period"] == "Q"]
                try:
                    res = MQ.calculer(exercices, trimestres, cours.get(company_id, []),
                                      capis.get(company_id), profil)
                    ser = MQ.serie_annuelle(exercices, profil)
                    if not complet:
                        # Sur treize mois, calculer() produirait une « pire baisse sur 5
                        # ans » qui n'en couvre qu'un : on reporte la valeur hebdomadaire.
                        for mid in MQ.HISTORIQUE_LONG:
                            res[mid] = conserves.get(company_id, {}).get(
                                mid, (None, "calculée au passage hebdomadaire"))
                except Exception as e:                  # une société ne tue pas le lot
                    r.fail(ticker=ticker, cik=cik, stage="calcul",
                           reason=type(e).__name__, detail=str(e))
                    continue
                for mid, (v, raison) in res.items():
                    lignes.append((company_id, aujourdhui, mid, v, raison))
                for mid, points in ser.items():
                    for fin, v in points:
                        series.append((company_id, mid, fin, v))
                calculees.append(company_id)

            try:
                db.ecrire_metriques(cn, calculees, lignes, series)
                cn.commit()
                ok += len(calculees)
                r.ok(len(calculees))
            except Exception as e:
                cn.rollback()
                for cid in calculees:
                    r.fail(stage="ecriture", reason=type(e).__name__, detail=f"company_id {cid}: {e}")
            log.info("  %d / %d", min(i + LOT, len(societes)), len(societes))

    log.info("%d sociétés calculées en %.1f s (%s)", ok, time.monotonic() - debut,
             "complet, cinq ans de cours" if complet else "quotidien, treize mois de cours")
    cn.close()
    return ok


def main() -> None:
    p = argparse.ArgumentParser(description="Calcul des métriques")
    p.add_argument("--tickers", type=str, default=None)
    p.add_argument("--complet", action="store_true",
                   help="cinq ans de cours et recalcul de tout : passage hebdomadaire")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    settings.setup_logging(a.verbose)
    settings.require_env()
    run([t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None, a.complet)


if __name__ == "__main__":
    main()
