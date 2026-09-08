"""
jobs/ingest_universe.py — Construction de l'univers (cadence mensuelle).

Passe A : liste des émetteurs, filtres EDGAR, enrichissement SIC.
Passe B : capitalisation et volume médian, sur la donnée déjà en base.

Les deux passes sont séparées par l'ingestion des cours et des fondamentaux : la
passe B ne peut pas s'exécuter avant eux, elle a besoin des cours pour le volume et
des actions EDGAR pour la capitalisation. `--finalize-only` la rejoue seule.

Usage :
    python -m jobs.ingest_universe              # passe A puis B
    python -m jobs.ingest_universe --finalize-only
"""
from __future__ import annotations
import argparse, logging
import psycopg2.extras as X
import db, settings, universe as U, mapping as M
from providers.edgar import EdgarApiProvider

log = logging.getLogger("ingest.universe")


def passe_a(cn, skip_sic: bool, limit_sic: int | None) -> int:
    edgar = EdgarApiProvider()
    filings = U.RecentFilings().load(edgar, cache_dir=str(settings.data_dir()))
    cands, funnel = U.build_candidates(edgar, filings)
    for k, v in funnel.items():
        log.info("  %-38s %6d", k, v)
    log.info("  candidats : %d lignes / %d CIK", len(cands), len({i.cik for i in cands}))

    with db.run(cn, "universe_a", "form.idx") as r:
        if not skip_sic:
            log.info("enrichissement SIC (~9 min pour l'univers complet)…")
            got = U.enrich_with_sic(edgar, cands, run=r, limit=limit_sic)
            log.info("  %d SIC récupérés", got)
        db.upsert_companies(cn, cands,
                            profiles={i.ticker: M.profile_from_sic(i.sic) for i in cands})
        with cn.cursor() as c:
            X.execute_values(c,
                "update companies set last_xbrl_filing = v.d::date, sic = v.s::smallint "
                "from (values %s) as v(t, d, s) where companies.ticker = v.t",
                [(i.ticker, filings.last[i.cik][0], i.sic) for i in cands], page_size=1000)
        r.ok(len(cands))
        cn.commit()
    edgar.close()
    return len(cands)


def main() -> None:
    p = argparse.ArgumentParser(description="Construction de l'univers")
    p.add_argument("--finalize-only", action="store_true")
    p.add_argument("--skip-sic", action="store_true",
                   help="passe A sans enrichissement SIC : les profils restent 'standard'")
    p.add_argument("--limit-sic", type=int, default=None)
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    settings.setup_logging(a.verbose)
    settings.require_env()

    cn = db.connect()
    if not a.finalize_only:
        passe_a(cn, a.skip_sic, a.limit_sic)
    log.info("passe B — capitalisation et volume médian")
    for k, v in U.finalize_universe(cn).items():
        log.info("  %-20s %s", k, v)
    cn.close()


if __name__ == "__main__":
    main()
