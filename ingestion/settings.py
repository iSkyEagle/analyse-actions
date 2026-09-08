"""
settings.py — Configuration et chemins.

Charge le .env depuis la racine du dépôt, quel que soit le dossier depuis lequel un
script est lancé. Sans ça, `python ingestion/jobs/ingest_prices.py` depuis la racine
et `python jobs/ingest_prices.py` depuis ingestion/ ne chargeraient pas le même
fichier — et le second ne chargerait rien du tout, silencieusement.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# ingestion/settings.py -> racine du dépôt
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

_loaded = load_dotenv(ROOT / ".env")


def require_env() -> None:
    """
    Échoue tôt et clairement. Découvrir au 4 000e ticker que SEC_USER_AGENT est absent
    coûte un lot entier ; le découvrir au démarrage coûte une seconde.
    """
    missing = [k for k in ("DATABASE_URL", "SEC_USER_AGENT") if not os.environ.get(k)]
    if missing:
        hint = "" if _loaded else f" (aucun .env trouvé dans {ROOT})"
        raise SystemExit(f"Variables manquantes : {', '.join(missing)}{hint}")


def setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # yfinance journalise abondamment ses tickers en échec ; on les traite nous-mêmes.
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR
