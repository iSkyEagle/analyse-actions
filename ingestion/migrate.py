"""
migrate.py — Applique un fichier de migration via DATABASE_URL.

L'éditeur SQL de Supabase coupe la connexion au bout de quelques secondes. Une
migration qui attend un verrou — une session restée `idle in transaction` après un
job interrompu suffit — y échoue systématiquement, avec un message qui n'indique pas
la cause. Ce script passe par la même connexion que les jobs et diagnostique lui-même.

Usage :
    python migrate.py ../migrations/0005_scale_traceability.sql
    python migrate.py --locks          # qui tient les verrous, sans rien appliquer
"""

from __future__ import annotations

import sys
from pathlib import Path

import os

import psycopg2

import settings


def montrer_verrous(cn) -> None:
    with cn.cursor() as c:
        c.execute("""
            select pid, state, left(query, 70) as requete,
                   round(extract(epoch from age(clock_timestamp(), state_change))) as depuis_s
            from pg_stat_activity
            where datname = current_database() and pid <> pg_backend_pid()
            order by state_change
        """)
        lignes = c.fetchall()
    if not lignes:
        print("aucune autre session ouverte")
        return
    print(f"{'pid':>8}  {'état':22}{'depuis':>8}  requête")
    for pid, state, q, depuis in lignes:
        marque = " <-- bloquante" if state == "idle in transaction" else ""
        print(f"{pid:>8}  {str(state):22}{depuis or 0:>7.0f}s  {q}{marque}")
    print("\nPour libérer : select pg_terminate_backend(<pid>);")


def appliquer(cn, chemin: Path) -> None:
    sql = chemin.read_text(encoding="utf-8")
    with cn.cursor() as c:
        # Échouer vite sur un verrou plutôt que d'attendre : le message sera clair.
        c.execute("set lock_timeout = '10s'")
        c.execute("set statement_timeout = '300s'")
        c.execute(sql)
    cn.commit()
    print(f"appliqué : {chemin.name}")


def main() -> None:
    settings.setup_logging()
    settings.require_env()
    cn = psycopg2.connect(os.environ["DATABASE_URL"])
    cn.autocommit = False
    try:
        if len(sys.argv) < 2 or sys.argv[1] == "--locks":
            montrer_verrous(cn)
            return
        chemin = Path(sys.argv[1])
        if not chemin.exists():
            raise SystemExit(f"introuvable : {chemin}")
        try:
            appliquer(cn, chemin)
        except psycopg2.errors.LockNotAvailable:
            cn.rollback()
            print("verrou indisponible — sessions en cours :\n")
            montrer_verrous(cn)
    finally:
        cn.close()


if __name__ == "__main__":
    main()
