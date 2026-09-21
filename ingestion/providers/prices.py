"""
providers/prices.py — Adaptateur de cours.

Deux règles du mémoire sont ici rendues **structurellement** impossibles à violer :

  1. « yfinance en appels groupés uniquement » — l'interface PriceProvider n'expose
     aucune méthode par ticker. Une boucle ticker-par-ticker ne compile pas.
  2. « yfinance ne touche jamais aux fondamentaux » — YFinanceProvider n'hérite pas de
     FundamentalsProvider et n'implémente ni list_issuers ni get_fundamentals.
     Le seul point d'entrée existant renvoie des cours.

La capitalisation n'est pas non plus lue ici : elle est calculée à partir des actions
EDGAR et du dernier cours, seul chemin traçable jusqu'au dépôt.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Iterator, Optional

from .base import FetchError, PriceProvider, PriceSeries

log = logging.getLogger(__name__)


def _colonne(df, champ: str, ticker: str):
    """
    Extrait la série d'un champ pour un ticker, quelle que soit la forme de la réponse.

    yfinance 1.x renvoie des colonnes à deux niveaux (Price, Ticker) MÊME pour un seul
    ticker. L'ancien code supposait une série simple pour un lot unitaire et recevait
    un tableau à une colonne : `float()` sur une ligne de ce tableau lève sous NumPy 2
    (« only 0-dimensional arrays can be converted to Python scalars »). Le lot
    multi-tickers n'y était pas exposé, d'où un bug invisible au premier test.
    """
    import pandas as pd
    col = df[champ]
    if isinstance(col, pd.DataFrame):
        if ticker in col.columns:
            col = col[ticker]
        elif col.shape[1] == 1:
            col = col.iloc[:, 0]
        else:
            raise KeyError(ticker)
    return col


class YFinanceProvider(PriceProvider):
    name = "yfinance"
    max_batch = 200          # au-delà, yfinance tronque silencieusement le lot

    def __init__(self, max_batch: Optional[int] = None):
        try:
            import yfinance as yf
        except ImportError as e:                       # pragma: no cover
            raise FetchError("yfinance non installé") from e
        self._yf = yf
        if max_batch:
            self.max_batch = max_batch

    def get_price_history(self, tickers: list, start: date, end: date) -> Iterator[PriceSeries]:
        """
        Découpe en lots, télécharge, et rend une série par ticker.

        Un ticker absent ou vide dans la réponse ne fait pas échouer le lot : il ressort
        avec ok=False et une raison, que l'appelant journalise dans ingestion_errors.
        C'est la contrainte « un ticker qui échoue ne doit pas interrompre le lot ».
        """
        import pandas as pd

        uniq = sorted({t for t in tickers if t})
        for i in range(0, len(uniq), self.max_batch):
            batch = uniq[i:i + self.max_batch]
            try:
                df = self._yf.download(
                    batch, start=start, end=end, interval="1d",
                    auto_adjust=True, progress=False, threads=True,
                    group_by="column", actions=False,
                )
            except Exception as e:
                # Lot entier perdu : on rend un échec par ticker plutôt que de lever,
                # pour que l'appelant garde la trace de ce qui n'a pas été récupéré.
                log.warning("lot yfinance en échec (%d tickers): %s", len(batch), e)
                for t in batch:
                    yield PriceSeries(t, [], ok=False, reason=f"lot: {type(e).__name__}")
                continue

            if df is None or df.empty:
                for t in batch:
                    yield PriceSeries(t, [], ok=False, reason="lot vide")
                continue

            for t in batch:
                # Chaque ticker est isolé dans son propre try. Une erreur de décodage
                # sur l'un d'eux ne doit JAMAIS remonter : elle tuerait le job entier
                # — constaté en production, trois backfills et deux runs quotidiens
                # perdus sur une seule exception de conversion.
                try:
                    close = _colonne(df, "Close", t)
                    volume = _colonne(df, "Volume", t)
                except KeyError:
                    yield PriceSeries(t, [], ok=False, reason="absent de la réponse")
                    continue
                try:
                    s = close.dropna()
                    if s.empty:
                        yield PriceSeries(t, [], ok=False, reason="série vide")
                        continue
                    v = volume.reindex(s.index).fillna(0)
                    days = [
                        (d.date() if hasattr(d, "date") else d, float(c), float(x))
                        for d, c, x in zip(s.index, s.to_numpy(), v.to_numpy())
                    ]
                except Exception as e:
                    yield PriceSeries(t, [], ok=False,
                                      reason=f"décodage: {type(e).__name__}: {str(e)[:80]}")
                    continue
                yield PriceSeries(t, days)
