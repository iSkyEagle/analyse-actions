"""
providers/base.py — Interfaces DataProvider et types normalisés.

La règle du mémoire est « aucun appel direct à une bibliothèque tierce dans la logique
métier ». Prise au pied de la lettre, elle mènerait à une interface qui renvoie du JSON
companyfacts brut — ce qui ne serait pas interchangeable du tout : le jour où on bascule
sur EODHD, toute la logique de lecture du XBRL resterait à réécrire côté appelant.

La frontière est donc placée plus haut : **un provider renvoie des lignes normalisées**,
pas de la donnée de source. Le désordre des balises US GAAP vit dans EdgarProvider et
n'en sort jamais. Un EodhdProvider ferait son propre décodage et renverrait les mêmes
FundamentalsRow. C'est cette frontière-là qui rend la bascule possible.

Conséquence : mapping.py et extract.py sont des détails d'implémentation d'EdgarProvider.
Rien d'autre dans le projet ne doit les importer.
"""

from __future__ import annotations

import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Iterator, Optional


# =============================================================================
# Erreurs — hiérarchie pensée pour la reprise sur erreur
# =============================================================================
class ProviderError(Exception):
    """Base. Toute erreur provider doit être attrapable sans connaître la source."""
    stage = "unknown"


class FetchError(ProviderError):
    """Réseau, HTTP, quota. Réessayable."""
    stage = "fetch"


class ParseError(ProviderError):
    """Donnée reçue mais inexploitable. Pas réessayable : rejouer donnera le même échec."""
    stage = "parse"


class OutOfScope(ProviderError):
    """L'émetteur existe mais ne relève pas de ce provider (IFRS, fonds, SPAC)."""
    stage = "scope"

    def __init__(self, profile: str, reason: str):
        super().__init__(f"{profile}: {reason}")
        self.profile = profile
        self.reason = reason


# =============================================================================
# Types normalisés — le contrat entre les providers et le reste du code
# =============================================================================
@dataclass(frozen=True)
class Issuer:
    cik: int
    ticker: str
    name: str
    exchange: str
    sic: Optional[int] = None     # renseigné par l'appelant, décide du mapping_profile


@dataclass
class FundamentalsRow:
    """Une ligne de la table fundamentals, indépendante de la source."""
    cik: int
    period_end: date
    fiscal_period: str            # 'Q' | 'FY'
    values: dict                  # {champ: valeur | None}
    accn: Optional[str] = None
    filed: Optional[date] = None
    inferred_zero: list = field(default_factory=list)
    q4_derived: bool = False


@dataclass
class FundamentalsResult:
    cik: int
    ticker: str
    mapping_profile: str
    rows: list
    data_quality_score: float
    fiscal_years: int
    shares_outstanding: Optional[float] = None
    shares_as_of: Optional[date] = None
    last_filing: Optional[date] = None
    unmapped_tags: list = field(default_factory=list)


@dataclass
class PriceSeries:
    ticker: str
    days: list                    # [(date, close_adj, volume)], croissant
    ok: bool = True
    reason: str = ""


# =============================================================================
# Interfaces
# =============================================================================
class FundamentalsProvider(ABC):
    """Fournit l'univers des émetteurs et leurs comptes, déjà normalisés."""

    name: str = "abstract"

    @abstractmethod
    def list_issuers(self) -> Iterator[Issuer]:
        ...

    @abstractmethod
    def get_fundamentals(self, issuer: Issuer) -> FundamentalsResult:
        """Lève OutOfScope, FetchError ou ParseError. Ne renvoie jamais de résultat vide muet."""

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class PriceProvider(ABC):
    """
    Fournit l'historique de cours. L'interface est **par lot et uniquement par lot** :
    il n'existe pas de méthode par ticker. C'est ce qui rend impossible, au niveau du
    type, la boucle ticker-par-ticker que le mémoire interdit.
    """

    name: str = "abstract"
    max_batch: int = 200

    @abstractmethod
    def get_price_history(self, tickers: list, start: date, end: date) -> Iterator[PriceSeries]:
        ...

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# =============================================================================
# Limiteur de débit — seau à jetons, sûr en multithread
# =============================================================================
class RateLimiter:
    """
    La SEC impose 10 requêtes/seconde maximum. Un sleep fixe entre appels gaspille du
    temps quand la requête précédente a été lente ; un seau à jetons ne dort que du
    strict nécessaire et tient si plusieurs threads appellent.
    """

    def __init__(self, rate_per_sec: float, burst: Optional[int] = None):
        self.rate = float(rate_per_sec)
        self.capacity = float(burst if burst is not None else rate_per_sec)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: float = 1.0) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                time.sleep((n - self._tokens) / self.rate)


# =============================================================================
# Configuration — jamais de secret en dur, jamais de valeur par défaut silencieuse
# =============================================================================
def sec_user_agent() -> str:
    """
    La SEC exige un en-tête `Nom Prénom email@domaine` et bloque sans. Une valeur par
    défaut ferait tomber le pipeline en production sans message clair : on échoue ici,
    à la configuration, plutôt qu'au 4000e ticker.
    """
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    if not ua or "@" not in ua:
        raise RuntimeError(
            "SEC_USER_AGENT absent ou mal formé. Format attendu : "
            "'Prénom Nom email@domaine'. Renseignez-le dans .env."
        )
    return ua


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url.startswith("postgres"):
        raise RuntimeError("DATABASE_URL absent. Chaîne Session pooler Supabase attendue.")
    return url
