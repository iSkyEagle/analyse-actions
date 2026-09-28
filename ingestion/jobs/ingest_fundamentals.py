"""
jobs/ingest_fundamentals.py — Ingestion des comptes.

Deux sources derrière la même interface, choisies par cadence et non par préférence :

  --source bulk   companyfacts.zip, 1,41 Go, régénéré chaque nuit. Une requête au lieu
                  de 4 500 : pas de limite de débit à gérer, pas d'échec réparti sur
                  des milliers d'appels, et une estampille de fraîcheur unique pour
                  tout le lot. C'est la source du rafraîchissement hebdomadaire.
                  L'archive dépliée pèse 15-20 Go, au-dessus des 14 Go d'un runner :
                  les membres sont lus un par un, jamais extraits.

  --source api    endpoint unitaire, pour rafraîchir quelques valeurs ou déboguer.

Reprise : les sociétés sont triées par date de dernier rafraîchissement croissante.
Un lot interrompu — plafond de 6 heures d'un job GitHub Actions — reprend là où il
s'est arrêté au passage suivant, sans état externe.

Usage :
    python -m jobs.ingest_fundamentals --source bulk
    python -m jobs.ingest_fundamentals --source api --tickers MSFT,CRBU
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date

import db
import settings
from providers.base import Issuer, OutOfScope, ProviderError
from providers.edgar import EdgarApiProvider, EdgarBulkProvider

log = logging.getLogger("ingest.fundamentals")

DUMP_MAX_AGE_DAYS = 6          # régénéré chaque nuit : au-delà d'une semaine, on retélécharge


def open_provider(source: str, refresh_dump: bool):
    if source == "api":
        return EdgarApiProvider(), "api"
    path = settings.data_dir() / "companyfacts.zip"
    stale = (not path.exists()
             or refresh_dump
             or (date.today() - date.fromtimestamp(path.stat().st_mtime)).days > DUMP_MAX_AGE_DAYS)
    if stale:
        log.info("téléchargement du dump (~1,41 Go), en flux…")
        EdgarBulkProvider.download(str(path))
        log.info("dump écrit : %.2f Go", path.stat().st_size / 1e9)
    provider = EdgarBulkProvider(str(path))
    return provider, provider.source_stamp


def run(source: str, limit: int | None, tickers: list | None, refresh_dump: bool,
        force: bool = False) -> int:
    cn = db.connect()
    provider, stamp = open_provider(source, refresh_dump)

    if tickers:
        with cn.cursor() as c:
            c.execute("select company_id, cik, ticker, sic, mapping_profile::text, "
                      "history_years from companies where ticker = any(%s)", (tickers,))
            rows = c.fetchall()
    else:
        # mappable_only=False : une société encore marquée 'standard' par défaut doit
        # pouvoir être reclassée en 'ifrs' ou 'spac' par ce passage.
        rows = db.select_universe(cn, in_universe=False, stale_first="fundamentals",
                                  limit=limit, mappable_only=False)
    log.info("%d sociétés à traiter (source %s)", len(rows), provider.name)

    n_rows, refreshed, ecartes, inchangees = 0, [], 0, 0
    # Dépôt le plus récent déjà ingéré, par lots de 200 CIK : une seule requête bornée
    # par lot, au lieu d'en poser une par société.
    deja: dict = {}
    with db.run(cn, "fundamentals", stamp) as r:
        for i, (company_id, cik, ticker, sic, profile, _) in enumerate(rows):
            if i % 200 == 0:
                deja = db.dernier_depot_ingere(cn, [x[1] for x in rows[i:i + 200]])
            issuer = Issuer(cik=cik, ticker=ticker, name=ticker, exchange="", sic=sic)
            try:
                res = provider.get_fundamentals(issuer)
            except OutOfScope as e:
                db.mark_out_of_scope(cn, ticker, e.profile)
                r.skip_provider(e, ticker=ticker, cik=cik)
                ecartes += 1
                try:
                    cn.commit()
                except Exception:
                    cn.rollback()
                continue
            except ProviderError as e:
                r.fail_provider(e, ticker=ticker, cik=cik)
                continue
            except Exception as e:                          # une société ne tue pas le lot
                cn.rollback()
                r.fail(ticker=ticker, cik=cik, stage="map",
                       reason=type(e).__name__, detail=str(e))
                continue
            # Aucun dépôt nouveau depuis le dernier passage : rien à écrire. Le passage
            # hebdomadaire relisait jusqu'ici 236 000 lignes pour en modifier quelques
            # milliers, et a dépassé ses trois heures le 27 septembre. `--force` lève ce
            # raccourci, pour appliquer un changement de logique d'extraction.
            connu = deja.get(cik)
            if not force and connu and res.last_filing and res.last_filing <= connu:
                inchangees += 1
                refreshed.append(cik)
                r.ok()
                continue
            try:
                n_rows += db.upsert_fundamentals(cn, res.rows, force=force)
                db.update_company_facts(cn, res, sic=sic)
            except Exception as e:
                cn.rollback()
                r.fail(ticker=ticker, cik=cik, stage="upsert",
                       reason=type(e).__name__, detail=str(e))
                continue
            refreshed.append(cik)
            r.ok()
            if len(refreshed) % 200 == 0:
                db.mark_refreshed(cn, "fundamentals", refreshed[-200:])
                cn.commit()
                log.info("  %d examinées, %d sans dépôt nouveau, %d lignes transmises",
                         len(refreshed), inchangees, n_rows)
        db.mark_refreshed(cn, "fundamentals", refreshed)
        cn.commit()

    # « transmises » et non « écrites » : la garde d'identité de la base n'écrit que les
    # lignes qui diffèrent réellement, et ce nombre-là n'est pas connu du client.
    log.info("%d sociétés examinées, dont %d sans dépôt nouveau ; %d lignes transmises ; "
             "%d écartées hors périmètre", len(refreshed), inchangees, n_rows, ecartes)
    provider.close()
    cn.close()
    return n_rows


def main() -> None:
    p = argparse.ArgumentParser(description="Ingestion des fondamentaux")
    p.add_argument("--source", choices=("bulk", "api"), default="bulk")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--tickers", type=str, default=None,
                   help="liste séparée par des virgules, force --source api")
    p.add_argument("--refresh-dump", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="réécrit même à date de dépôt inchangée : à utiliser après un "
                        "changement de logique d'extraction")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()

    settings.setup_logging(a.verbose)
    settings.require_env()
    tickers = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else None
    run("api" if tickers else a.source, a.limit, tickers, a.refresh_dump, a.force)


if __name__ == "__main__":
    main()
