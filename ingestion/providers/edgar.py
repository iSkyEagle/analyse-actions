"""
providers/edgar.py — Adaptateurs EDGAR.

Deux modes d'accès, une seule signature :
  * EdgarApiProvider  — endpoint unitaire, pour un ticker isolé ou le développement
  * EdgarBulkProvider — dump companyfacts.zip (1,41 Go), pour le rafraîchissement complet

Les deux héritent de _EdgarBase, qui porte tout le décodage. Ils ne diffèrent que par
`_raw_facts()`. Le reste du projet ne sait pas lequel il utilise.
"""

from __future__ import annotations

import io
import json
import logging
import os
import zipfile
from datetime import date, datetime
from typing import Iterator, Optional

import requests
from tenacity import (retry, retry_if_exception_type, stop_after_attempt,
                      wait_exponential)

import mapping as M
import extract as E
from .base import (FetchError, FundamentalsProvider, FundamentalsResult,
                   FundamentalsRow, Issuer, OutOfScope, ParseError, RateLimiter,
                   sec_user_agent)

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

# Places retenues. Le fichier SEC ne distingue pas NYSE American de NYSE : les deux
# sortent étiquetées 'NYSE'. Ce n'est pas un problème, aucun filtre n'en dépend.
EXCHANGES = {"Nasdaq", "NYSE", "CBOE"}


def _d(s) -> Optional[date]:
    return datetime.strptime(s, "%Y-%m-%d").date() if s else None


class _EdgarBase(FundamentalsProvider):
    """Décodage commun. Les sous-classes fournissent seulement la donnée brute."""

    def __init__(self, session: Optional[requests.Session] = None):
        self.limiter = RateLimiter(rate_per_sec=9, burst=9)   # marge sous les 10/s SEC
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": sec_user_agent(),
            "Accept-Encoding": "gzip, deflate",
        })

    # -- accès HTTP mutualisé -------------------------------------------------
    @retry(retry=retry_if_exception_type(FetchError),
           stop=stop_after_attempt(4),
           wait=wait_exponential(multiplier=1, min=1, max=20),
           reraise=True)
    def _get(self, url: str, **kw) -> requests.Response:
        self.limiter.acquire()
        try:
            r = self.session.get(url, timeout=kw.pop("timeout", 120), **kw)
        except requests.RequestException as e:
            raise FetchError(f"{type(e).__name__} sur {url}") from e
        if r.status_code in (429, 500, 502, 503, 504):
            raise FetchError(f"HTTP {r.status_code} sur {url}")
        if r.status_code == 404:
            raise ParseError(f"404 sur {url}")
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code} sur {url}")
        return r

    # -- univers --------------------------------------------------------------
    def list_issuers(self) -> Iterator[Issuer]:
        payload = self._get(TICKERS_URL).json()
        fields = payload["fields"]
        i_cik, i_name = fields.index("cik"), fields.index("name")
        i_tk, i_ex = fields.index("ticker"), fields.index("exchange")
        for row in payload["data"]:
            if row[i_ex] not in EXCHANGES:
                continue
            yield Issuer(cik=int(row[i_cik]), ticker=row[i_tk],
                         name=row[i_name], exchange=row[i_ex])

    def get_sic(self, cik: int) -> Optional[int]:
        try:
            s = self._get(SUBS_URL.format(cik=cik)).json()
        except ParseError:
            return None                     # émetteur sans page submissions : profil par défaut
        try:
            return int(s.get("sic") or 0) or None
        except (TypeError, ValueError):
            return None

    # -- à implémenter par les sous-classes -----------------------------------
    def _raw_facts(self, cik: int) -> dict:
        raise NotImplementedError

    # -- normalisation --------------------------------------------------------
    def get_fundamentals(self, issuer: Issuer) -> FundamentalsResult:
        facts = self._raw_facts(issuer.cik)
        if not facts:
            raise ParseError(f"companyfacts vide pour CIK {issuer.cik}")

        profile_out, reason = M.out_of_scope(facts, issuer.sic)
        if profile_out:
            raise OutOfScope(profile_out, reason)

        profile = M.profile_from_sic(issuer.sic)
        try:
            raw_rows, elog = E.extract(facts, issuer.cik, issuer.ticker, profile)
        except Exception as e:
            raise ParseError(f"extraction: {type(e).__name__}: {e}") from e

        rows = [
            FundamentalsRow(
                cik=issuer.cik,
                period_end=_d(r["period_end"]),
                fiscal_period=r["fiscal_period"],
                values={k: v for k, v in r.items()
                        if k in M.STORED and not k.startswith("_")},
                accn=r.get("accn"),
                filed=_d(r.get("filed")),
                inferred_zero=r.get("_inferred_zero") or [],
                q4_derived=bool(r.get("q4_derived")),
                scale_corrected=r.get("_scale_corrected") or [],
            )
            for r in raw_rows
        ]

        dei = (facts.get("facts", {}).get("dei", {})
                    .get("EntityCommonStockSharesOutstanding", {})
                    .get("units", {}).get("shares", []))
        shares = shares_at = None
        if dei:
            last = max(dei, key=lambda e: (e["end"], e["filed"]))
            shares, shares_at = last["val"], _d(last["end"])

        return FundamentalsResult(
            cik=issuer.cik,
            ticker=issuer.ticker,
            mapping_profile=profile,
            rows=rows,
            data_quality_score=E.data_quality_score(raw_rows, profile),
            fiscal_years=elog.fiscal_years,
            shares_outstanding=shares,
            shares_as_of=shares_at,
            last_filing=max((r.filed for r in rows if r.filed), default=None),
            unmapped_tags=elog.unmapped_frequent_tags,
        )

    def close(self) -> None:
        self.session.close()




class EdgarApiProvider(_EdgarBase):
    """Endpoint unitaire. ~2 Mo par émetteur, 9 req/s : à réserver aux lots restreints."""

    name = "edgar-api"

    def _raw_facts(self, cik: int) -> dict:
        return self._get(FACTS_URL.format(cik=cik)).json()


class EdgarBulkProvider(_EdgarBase):
    """
    Dump companyfacts.zip. Une requête au lieu de 8 000, pas de risque de limite de débit,
    pas d'échec partiel réparti sur des milliers d'appels, et une estampille de fraîcheur
    unique pour tout le lot.

    L'archive dépliée pèse 15-20 Go, au-dessus des 14 Go d'un runner GitHub Actions :
    les membres sont donc lus un par un via ZipFile.open(), jamais extraits.
    """

    name = "edgar-bulk"
    MEMBER = "CIK{cik:010d}.json"

    def __init__(self, zip_path: str, session: Optional[requests.Session] = None):
        super().__init__(session)
        if not os.path.exists(zip_path):
            raise FetchError(f"dump absent: {zip_path}. Appeler download() d'abord.")
        self.zip_path = zip_path
        self._zf = zipfile.ZipFile(zip_path)
        self._members = set(self._zf.namelist())
        self.source_stamp = f"{os.path.basename(zip_path)}@{date.fromtimestamp(os.path.getmtime(zip_path))}"

    @classmethod
    def download(cls, dest: str, session: Optional[requests.Session] = None,
                 chunk: int = 1 << 22) -> str:
        """Téléchargement en flux : le fichier ne passe jamais entièrement en mémoire."""
        s = session or requests.Session()
        s.headers.update({"User-Agent": sec_user_agent()})
        tmp = dest + ".part"
        with s.get(BULK_URL, stream=True, timeout=600) as r:
            if r.status_code != 200:
                raise FetchError(f"HTTP {r.status_code} sur le dump")
            with open(tmp, "wb") as f:
                for block in r.iter_content(chunk_size=chunk):
                    f.write(block)
        os.replace(tmp, dest)          # remplacement atomique : jamais de dump tronqué
        return dest

    def _raw_facts(self, cik: int) -> dict:
        member = self.MEMBER.format(cik=cik)
        if member not in self._members:
            raise ParseError(f"{member} absent du dump")
        try:
            with self._zf.open(member) as fh:
                return json.load(io.TextIOWrapper(fh, encoding="utf-8"))
        except (zipfile.BadZipFile, json.JSONDecodeError) as e:
            raise ParseError(f"{member}: {type(e).__name__}") from e

    def known_ciks(self) -> set:
        return {int(n[3:-5]) for n in self._members
                if n.startswith("CIK") and n.endswith(".json")}

    def close(self) -> None:
        self._zf.close()
        super().close()
