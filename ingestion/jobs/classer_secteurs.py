"""
jobs/classer_secteurs.py — Applique la taxonomie de secteurs.py à toutes les sociétés.

Le passage hebdomadaire complet des métriques le fait déjà ; cette commande sert après
une modification de secteurs.py, pour l'appliquer sans attendre dimanche.

Usage :
    python -m jobs.classer_secteurs
"""
import logging

import db
import settings

log = logging.getLogger("ingest.secteurs")


def main() -> None:
    settings.setup_logging(False)
    settings.require_env()
    cn = db.connect()
    n = db.appliquer_secteurs(cn)
    cn.commit()
    with cn.cursor() as c:
        c.execute("""select sector, count(*), count(distinct industry) from companies
                     where in_universe group by 1 order by 2 desc""")
        repartition = c.fetchall()
    cn.close()
    log.info("%d société(s) reclassée(s). Univers par secteur :", n)
    for s, nb, ni in repartition:
        log.info("  %-28s %5d sociétés, %2d industries", s, nb, ni)


if __name__ == "__main__":
    main()
