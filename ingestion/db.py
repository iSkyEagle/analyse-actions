"""
db.py — Persistance. Seul module qui connaît psycopg2 et le schéma SQL.

Porte les trois garanties du plan de phase 1 :
  * idempotence   — relancer un script ne duplique ni ne perd rien
  * retraitement  — un dépôt plus récent écrase, un dépôt plus ancien est ignoré
  * journal       — quel ticker a échoué, à quelle étape, et pourquoi
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date
from typing import Iterable, Optional

import psycopg2
import psycopg2.extras as X
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import mapping as M
from providers.base import (FundamentalsResult, PriceSeries, ProviderError,
                            database_url)

log = logging.getLogger(__name__)

# Colonnes numériques de fundamentals, dans l'ordre du schéma.
FUND_COLS = [n for n in M.STORED if not n.startswith("_")]


def _signaler_tentative(etat) -> None:
    log.warning("connexion refusée (%s), nouvelle tentative dans %.0f s — tentative %d",
                type(etat.outcome.exception()).__name__, etat.next_action.sleep, etat.attempt_number)


# Cinq nouvelles tentatives espacées de 15 s à 2 min, soit ~5 minutes au total.
# Constaté : l'instance nano traverse des indisponibilités passagères, puis redevient
# disponible — le backfill du 26 a réussi entre deux pannes. Sans ces tentatives, un
# job tombé sur deux minutes d'indisponibilité échouait pour la nuit entière.
# Seules les erreurs de CONNEXION sont réessayées : une requête qui échoue en cours de
# job reste une erreur, le job suivant reprendra là où il s'est arrêté.
@retry(retry=retry_if_exception_type(psycopg2.OperationalError),
       stop=stop_after_attempt(6),
       wait=wait_exponential(multiplier=15, min=15, max=120),
       before_sleep=_signaler_tentative,
       reraise=True)
def connect(url: Optional[str] = None):
    cn = psycopg2.connect(url or database_url())
    cn.autocommit = False
    return cn


# =============================================================================
# Textes de requête CONSTANTS
#
# `execute_values` et les listes Python adaptées en ARRAY[...] inscrivent chaque valeur
# dans le texte SQL. L'extension pg_stat_statements, que la supervision de Supabase
# interroge en continu, recense alors une requête DIFFÉRENTE pour chaque nombre de
# lignes — une par société, puisque chacune a un nombre d'exercices différent. Mesuré :
# 46 entrées et 1,2 Mo de texte pour 40 sociétés seulement, jusqu'à 55 Ko par requête.
# Sur l'univers réel, la supervision de Supabase mettait 11 à 17 secondes à relire ces
# textes sur l'instance nano.
#
# Deux règles, appliquées à toutes les écritures groupées :
#   - les données passent par COPY dans une table de transit, puis une instruction
#     d'insertion au texte constant ;
#   - les listes d'identifiants passent comme UN SEUL littéral '{1,2,3}', jamais comme
#     ARRAY[1,2,3], qui compterait autant de constantes que d'éléments.
# =============================================================================
import io


def _echappe_copie(s: str) -> str:
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def _litteral_tableau(valeurs) -> str:
    """Tableau Postgres sous forme de littéral texte : '{1,2}', '{"a","b"}'."""
    elements = []
    for x in valeurs:
        if x is None:
            elements.append("NULL")
        elif isinstance(x, bool):
            elements.append("t" if x else "f")
        elif isinstance(x, (int, float)):
            elements.append(repr(float(x)) if isinstance(x, float) else str(x))
        elif isinstance(x, date):
            elements.append(x.isoformat())
        else:
            s = str(x).replace("\\", "\\\\").replace('"', '\\"')
            elements.append(f'"{s}"')
    return "{" + ",".join(elements) + "}"


def tableau(valeurs) -> str:
    """À passer en paramètre avec un cast explicite : `= any(%s::int[])`."""
    return _litteral_tableau(list(valeurs))


def _valeur_copie(v) -> str:
    if v is None:
        return "\\N"
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, (list, tuple)):
        return _echappe_copie(_litteral_tableau(v))
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return repr(v)
    return _echappe_copie(str(v))


def copier(c, table: str, colonnes: list, lignes) -> int:
    """COPY de lignes dans une table : un seul texte de requête, quel que soit le volume."""
    tampon = io.StringIO()
    n = 0
    for ligne in lignes:
        tampon.write("\t".join(_valeur_copie(v) for v in ligne))
        tampon.write("\n")
        n += 1
    tampon.seek(0)
    c.copy_expert(f"copy {table} ({', '.join(colonnes)}) from stdin", tampon)
    return n


def _clean(v):
    """Postgres refuse NaN et Infinity en double precision : on les traite en absence."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or abs(f) > 1e300:
        return None
    return f


# =============================================================================
# Journal d'ingestion
# =============================================================================
class Run:
    """Un passage d'un job. Ouvre une ligne ingestion_runs, la ferme à la sortie."""

    def __init__(self, cn, job: str, source_stamp: str = ""):
        self.cn, self.job = cn, job
        self.n_ok = self.n_failed = self.n_skipped = 0
        with cn.cursor() as c:
            c.execute("insert into ingestion_runs (job, source_stamp) values (%s,%s) "
                      "returning run_id", (job, source_stamp))
            self.run_id = c.fetchone()[0]
        cn.commit()

    def fail(self, *, ticker=None, cik=None, stage="unknown", reason="", detail=""):
        """
        Journalise un échec. Robuste à une transaction déjà avortée : sans ça, l'erreur
        qu'on cherche à tracer empêcherait d'écrire sa propre trace, et le lot mourrait
        au moment précis où le journal devient utile.
        """
        self.n_failed += 1
        args = (self.run_id, ticker, cik, stage, reason[:200], (detail or "")[:1000])
        sql = ("insert into ingestion_errors (run_id,ticker,cik,stage,reason,detail) "
               "values (%s,%s,%s,%s,%s,%s)")
        for tentative in (1, 2):
            try:
                with self.cn.cursor() as c:
                    c.execute(sql, args)
                return
            except Exception as e:
                self.cn.rollback()          # purge l'état avorté puis réessaie une fois
                if tentative == 2:
                    log.error("journal indisponible pour %s : %s", ticker, e)

    def fail_provider(self, exc: ProviderError, *, ticker=None, cik=None):
        self.fail(ticker=ticker, cik=cik, stage=getattr(exc, "stage", "unknown"),
                  reason=type(exc).__name__, detail=str(exc))

    def skip_provider(self, exc: ProviderError, *, ticker=None, cik=None):
        """
        Écart hors périmètre : journalisé pour la traçabilité, mais compté à part.
        Compter une société IFRS comme un échec faisait lire « 396 en échec » là où il
        n'y avait que des exclusions attendues.
        """
        self.fail(ticker=ticker, cik=cik, stage=getattr(exc, "stage", "scope"),
                  reason=type(exc).__name__, detail=str(exc))
        self.n_failed -= 1
        self.n_skipped += 1

    def ok(self, n: int = 1):
        self.n_ok += n

    def close(self, notes: str = ""):
        if self.n_skipped and not notes:
            notes = f"{self.n_skipped} écartées hors périmètre"
        try:
            with self.cn.cursor() as c:
                c.execute("update ingestion_runs set finished_at=now(), n_ok=%s, "
                          "n_failed=%s, notes=%s where run_id=%s",
                          (self.n_ok, self.n_failed, notes or None, self.run_id))
            self.cn.commit()
        except Exception as e:                  # la clôture du journal ne doit rien casser
            self.cn.rollback()
            log.error("clôture du run %s impossible : %s", self.run_id, e)
        log.info("[%s] run %s : %d ok, %d en échec, %d écartées", self.job, self.run_id,
                 self.n_ok, self.n_failed, self.n_skipped)


@contextmanager
def run(cn, job: str, source_stamp: str = ""):
    r = Run(cn, job, source_stamp)
    try:
        yield r
    finally:
        r.close()


# =============================================================================
# companies
# =============================================================================
def upsert_companies(cn, issuers, profiles: dict = None) -> dict:
    """Insère ou met à jour les lignes de cotation. Retourne {ticker: company_id}."""
    profiles = profiles or {}
    rows = [(i.cik, i.ticker, i.name[:200], i.exchange,
             profiles.get(i.ticker, "standard")) for i in issuers]
    with cn.cursor() as c:
        X.execute_values(c, """
            insert into companies (cik, ticker, name, exchange, mapping_profile) values %s
            on conflict (ticker) do update
              set cik = excluded.cik, name = excluded.name,
                  exchange = excluded.exchange,
                  mapping_profile = excluded.mapping_profile,
                  updated_at = now()
        """, rows, page_size=1000)
        c.execute("select ticker, company_id from companies")
        return dict(c.fetchall())


def update_company_facts(cn, res: FundamentalsResult, sic: Optional[int] = None) -> None:
    with cn.cursor() as c:
        c.execute("""
            update companies set
              sic = coalesce(%s, sic),
              mapping_profile = %s,
              shares_outstanding = %s,
              shares_as_of = %s,
              last_xbrl_filing = %s,
              data_quality_score = %s,
              fiscal_years_available = %s,
              updated_at = now()
            where ticker = %s
        """, (sic, res.mapping_profile, _clean(res.shares_outstanding), res.shares_as_of,
              res.last_filing, res.data_quality_score, res.fiscal_years, res.ticker))


def refresh_market_caps(cn) -> int:
    """
    Capitalisation = actions EDGAR × dernier cours. Jamais lue chez un fournisseur de
    cours : c'est le seul chemin traçable jusqu'au dépôt.
    Marque incertaines les lignes multi-classes, où companyfacts, qui supprime les
    dimensions, peut n'avoir renvoyé qu'une seule classe.
    """
    with cn.cursor() as c:
        # Le filtre porte sur prices.ym AVANT de déplier les tableaux : la vue
        # prices_daily ne peut pas pousser un prédicat à l'intérieur d'un tableau, et
        # sans cette borne on déplierait les ~5 M de séances de l'univers pour n'en
        # garder qu'une par société — trop lourd pour une instance nano.
        c.execute("""
            with recent as (
              select company_id, ym, d, c, v from prices
              where ym >= date_trunc('month', current_date - interval '2 months')::date
            ),
            dernier as (
              select distinct on (r.company_id) r.company_id,
                     (r.ym + (t.day - 1))::date as dt, t.close_adj
              from recent r, unnest(r.d, r.c, r.v) as t(day, close_adj, volume)
              order by r.company_id, dt desc
            ),
            multi as (select cik from companies group by cik having count(*) > 1)
            update companies c set
              market_cap = c.shares_outstanding * d.close_adj,
              market_cap_as_of = d.dt,
              market_cap_uncertain = (c.cik in (select cik from multi)),
              updated_at = now()
            from dernier d
            where d.company_id = c.company_id and c.shares_outstanding is not null
        """)
        return c.rowcount


# =============================================================================
# fundamentals — la clause qui règle idempotence et retraitement d'un coup
# =============================================================================
_SET = ", ".join(f"{c} = excluded.{c}" for c in FUND_COLS)

# Colonnes dont un changement justifie une réécriture. `ingested_at` n'en fait pas
# partie : c'est précisément lui qui changeait à chaque passage.
_DONNEES = ["fiscal_year"] + FUND_COLS + ["accn", "filed", "inferred_zero", "q4_derived", "scale_corrected"]
_CHANGE = (f"({', '.join('fundamentals.' + c for c in _DONNEES)}) is distinct from "
           f"({', '.join('excluded.' + c for c in _DONNEES)})")

_COLS_ECRITES = ["cik", "period_end", "fiscal_period", "fiscal_year"] + FUND_COLS + [
    "accn", "filed", "inferred_zero", "q4_derived", "scale_corrected"]

_UPSERT_BASE = f"""
insert into fundamentals ({', '.join(_COLS_ECRITES)})
select {', '.join(_COLS_ECRITES)} from _fund_stage
on conflict (cik, period_end, fiscal_period) do update set
  fiscal_year = excluded.fiscal_year,
  {_SET},
  accn = excluded.accn,
  filed = excluded.filed,
  inferred_zero = excluded.inferred_zero,
  q4_derived = excluded.q4_derived,
  scale_corrected = excluded.scale_corrected,
  ingested_at = now()
"""

# Deux conditions cumulées :
#   - `filed >=` : un dépôt antérieur n'écrase jamais un dépôt plus récent (retraitement).
#     `>=` et non `>`, pour qu'une extraction corrigée du même dépôt puisse s'appliquer.
#   - `is distinct from` : une ligne identique n'est PAS réécrite. Dans Postgres, une mise
#     à jour écrit une nouvelle version et laisse l'ancienne à nettoyer : réécrire à
#     l'identique coûte autant qu'une vraie modification, deux fois. Le passage
#     hebdomadaire réécrivait ~262 000 lignes à 99 % inchangées, et l'attente disque qui
#     en résultait saturait l'instance nano — connexions refusées la nuit suivante.
UPSERT_FUNDAMENTALS = _UPSERT_BASE + f"""
where excluded.filed is not null
  and (fundamentals.filed is null or excluded.filed >= fundamentals.filed)
  and {_CHANGE}
"""

# Réingestion forcée : relâche la seule garde de retraitement, garde celle d'identité.
UPSERT_FUNDAMENTALS_FORCE = _UPSERT_BASE + f"""
where {_CHANGE}
"""


def upsert_fundamentals(cn, rows: Iterable, force: bool = False) -> int:
    payload = [
        # fiscal_year = année de la date de clôture. Convention simple et stable ; le
        # champ `fy` d'EDGAR désigne l'exercice du DÉPÔT, pas celui de la période.
        tuple([r.cik, r.period_end, r.fiscal_period, r.period_end.year]
              + [_clean(r.values.get(col)) for col in FUND_COLS]
              + [r.accn, r.filed, list(r.inferred_zero or []), bool(r.q4_derived),
                 list(getattr(r, "scale_corrected", None) or [])])
        for r in rows
    ]
    if not payload:
        return 0
    sql = UPSERT_FUNDAMENTALS_FORCE if force else UPSERT_FUNDAMENTALS
    with cn.cursor() as c:
        c.execute("create temp table if not exists _fund_stage "
                  "(like fundamentals including defaults) on commit drop")
        c.execute("truncate _fund_stage")
        copier(c, "_fund_stage", _COLS_ECRITES, payload)
        c.execute(sql)
    return len(payload)


# =============================================================================
# prices — un appel de fonction par mois touché, fusion idempotente côté base
# =============================================================================
def _months_of(company_id: int, series: PriceSeries) -> list:
    """Découpe une série en tuples (company_id, ym, jours[], clôtures[], volumes[])."""
    if not series.ok or not series.days:
        return []
    mois: dict = {}
    for d, close, vol in series.days:
        mois.setdefault((d.year, d.month), []).append((d.day, close, vol))
    out = []
    for (y, m), vals in mois.items():
        vals.sort()
        out.append((company_id, date(y, m, 1),
                    [int(v[0]) for v in vals],
                    [float(v[1]) for v in vals],
                    [float(v[2]) for v in vals]))
    return out


def upsert_prices(cn, company_id: int, series: PriceSeries) -> int:
    """Voie unitaire, conservée pour les tests et le rafraîchissement d'une valeur."""
    return upsert_prices_bulk(cn, _months_of(company_id, series))


# Une instruction pour des milliers de mois, au lieu d'un appel de fonction par mois.
#
# La première version appelait merge_price_month() par société ET par mois : sur un
# backfill de 4 500 sociétés sur 5 ans, cela fait ~270 000 appels PL/pgSQL, chacun avec
# sa jointure externe et ses array_agg. Sur le CPU à crédits de rafale du tier gratuit
# Supabase, les crédits s'épuisent et l'instance retombe à son débit de base : tout
# passe en statement timeout, y compris les requêtes du dashboard.
#
# La fusion est ici faite en base, en une passe, via une table temporaire. La sémantique
# est identique — à quantième égal la nouvelle valeur gagne, le reste du mois est
# conservé — donc l'idempotence est préservée.
MERGE_BULK = """
insert into prices (company_id, ym, d, c, v)
select s.company_id, s.ym, m.d, m.c, m.v
from _px_stage s
left join prices p on p.company_id = s.company_id and p.ym = s.ym
cross join lateral (
    select array_agg(jour order by jour)::smallint[] as d,
           array_agg(cloture order by jour)::real[]  as c,
           array_agg(volume  order by jour)::real[]  as v
    from (
        select coalesce(n.jour, o.jour)       as jour,
               coalesce(n.cloture, o.cloture) as cloture,
               coalesce(n.volume, o.volume)   as volume
        from unnest(s.d, s.c, s.v) as n(jour, cloture, volume)
        full outer join unnest(coalesce(p.d, '{}'::smallint[]),
                               coalesce(p.c, '{}'::real[]),
                               coalesce(p.v, '{}'::real[])) as o(jour, cloture, volume)
          using (jour)
    ) fusion
) m
on conflict (company_id, ym) do update
    set d = excluded.d, c = excluded.c, v = excluded.v
    -- Un mois dont la fusion ne change rien n'est pas réécrit. Le rattrapage quotidien
    -- refusionne plusieurs mois déjà complets : sans cette garde, chacun devenait une
    -- nouvelle version de ligne et une ancienne à nettoyer.
    where (prices.d, prices.c, prices.v) is distinct from (excluded.d, excluded.c, excluded.v)
"""


def upsert_prices_bulk(cn, mois: Iterable) -> int:
    """`mois` = itérable de (company_id, ym, jours[], clôtures[], volumes[])."""
    lignes = list(mois)
    if not lignes:
        return 0
    with cn.cursor() as c:
        c.execute("create temp table if not exists _px_stage "
                  "(company_id smallint, ym date, d smallint[], c real[], v real[]) "
                  "on commit drop")
        c.execute("truncate _px_stage")
        copier(c, "_px_stage", ["company_id", "ym", "d", "c", "v"], lignes)
        c.execute(MERGE_BULK)
    return len(lignes)


def prune_price_history(cn, company_id: int, years: int) -> int:
    """Applique la profondeur d'historique visée. Le schéma ne plafonne rien lui-même."""
    with cn.cursor() as c:
        c.execute("delete from prices where company_id = %s "
                  "and ym < (current_date - make_interval(years => %s))",
                  (company_id, years))
        return c.rowcount


# =============================================================================
# Sélection et état de fraîcheur
# =============================================================================
MAPPABLE = ("standard", "financial", "reit")

# Miroir de l'énumération mapping_profile_t. Une valeur inconnue ferait échouer
# l'UPDATE, ce qui empoisonnerait la transaction et tuerait le lot entier — c'est
# arrivé en production sur 'unknown'. On dégrade plutôt que d'échouer.
PROFILES = frozenset(("standard", "financial", "reit", "ifrs", "foreign",
                      "fund", "spac", "unknown"))


def select_universe(cn, in_universe: bool = True, stale_first: Optional[str] = None,
                    limit: Optional[int] = None, mappable_only: bool = True) -> list:
    """
    Retourne [(company_id, cik, ticker, sic, mapping_profile, history_years)].

    `stale_first` prend 'fundamentals' ou 'prices' : le tri place en tête les sociétés
    jamais traitées puis les plus anciennes. C'est ce qui rend un lot interrompu
    reprenable sans état externe — le curseur est la donnée elle-même.
    """
    where = ["true"]
    if in_universe:
        where.append("in_universe")
    if mappable_only:
        where.append("mapping_profile in %(mappable)s")
    order = ""
    if stale_first in ("fundamentals", "prices"):
        order = f"order by {stale_first}_refreshed_at asc nulls first, company_id"
    sql = (f"select company_id, cik, ticker, sic, mapping_profile::text, history_years "
           f"from companies where {' and '.join(where)} {order} "
           f"{'limit %(limit)s' if limit else ''}")
    with cn.cursor() as c:
        c.execute(sql, {"mappable": MAPPABLE, "limit": limit})
        return c.fetchall()


def mark_out_of_scope(cn, ticker: str, profile: str) -> bool:
    """
    Persiste le profil hors périmètre remonté par out_of_scope(). Sans ça, une société
    IFRS reste marquée 'standard' et n'est écartée de l'univers que par accident
    d'absence de capitalisation — un filtre juste pour une mauvaise raison.

    Ne lève jamais : un profil non reconnu retombe sur 'unknown', et un échec SQL est
    annulé proprement. Écarter la société reste acquis dans tous les cas, puisque
    in_universe est mis à faux dans la même instruction.
    """
    if profile not in PROFILES:
        log.warning("profil hors énumération '%s' pour %s, dégradé en 'unknown'",
                    profile, ticker)
        profile = "unknown"
    # `fundamentals_refreshed_at` est renseigné ici AUSSI : une société hors périmètre
    # a bien été examinée. Sans ça elle garde NULL, remonte en tête du curseur de
    # reprise — qui trie `nulls first` — et se fait réexaminer à chaque lancement.
    # Constaté en production : un passage de 400 n'atteignait que 4 sociétés utiles,
    # les 396 autres étant des exclusions déjà connues.
    try:
        with cn.cursor() as c:
            c.execute("update companies set mapping_profile = %s::mapping_profile_t, "
                      "in_universe = false, fundamentals_refreshed_at = current_date, "
                      "updated_at = now() where ticker = %s returning cik",
                      (profile, ticker))
            row = c.fetchone()
            # Une société hors périmètre ne doit pas garder de comptes en base. Sans
            # cette purge, les lignes écrites avant son reclassement survivent
            # indéfiniment : le job l'écarte désormais avant extraction, donc plus
            # rien ne les réécrit ni ne les corrige. Constaté en production sur des
            # Q4 aux nombres d'actions erronés, restés après le correctif.
            if row:
                c.execute("delete from fundamentals where cik = %s", (row[0],))
        return True
    except Exception as e:
        cn.rollback()
        log.error("mark_out_of_scope(%s, %s) : %s", ticker, profile, e)
        with cn.cursor() as c:
            c.execute("update companies set in_universe = false, "
                      "fundamentals_refreshed_at = current_date where ticker = %s", (ticker,))
        return False


def mark_refreshed(cn, kind: str, ids: Iterable, on: Optional[date] = None) -> int:
    """kind = 'fundamentals' (clé cik) ou 'prices' (clé company_id)."""
    col = {"fundamentals": ("fundamentals_refreshed_at", "cik"),
           "prices": ("prices_refreshed_at", "company_id")}[kind]
    ids = list(ids)
    if not ids:
        return 0
    with cn.cursor() as c:
        c.execute(f"update companies set {col[0]} = %s where {col[1]} = any(%s::bigint[])",
                  (on or date.today(), tableau(ids)))
        return c.rowcount


def price_coverage(cn, company_ids: list) -> dict:
    """
    {company_id: (premier_mois, dernier_mois)} pour les sociétés demandées.

    Deux sondages d'index par société, sur la clé primaire (company_id, ym), et jamais
    d'agrégat sur la table entière. La première version faisait un `group by` sur
    TOUS les cours — 82 Mo de tableaux lus à chaque passage — et dépassait le délai
    d'exécution de l'instance nano : les runs quotidiens échouaient sur un
    `canceling statement due to statement timeout`. Le coût dépend désormais du nombre
    de sociétés interrogées, plus de la taille de l'historique.
    """
    if not company_ids:
        return {}
    with cn.cursor() as c:
        c.execute("""
            select s.company_id, debut.ym, fin.ym
            from unnest(%s::smallint[]) as s(company_id)
            cross join lateral (select ym from prices p where p.company_id = s.company_id
                                order by ym asc limit 1) debut
            cross join lateral (select ym from prices p where p.company_id = s.company_id
                                order by ym desc limit 1) fin
        """, (tableau(company_ids),))
        return {r[0]: (r[1], r[2]) for r in c.fetchall()}


# =============================================================================
# Métriques — lecture par lots, écriture idempotente
# =============================================================================
def charger_fondamentaux(cn, ciks: list) -> dict:
    """{cik: [ligne]} pour un lot de sociétés, en une requête.

    Les dates sont rendues en chaîne 'AAAA-MM-JJ' : le moteur de métriques les compare
    et les soustrait sous cette forme, indépendamment du pilote de base.
    """
    if not ciks:
        return {}
    cols = ["cik", "period_end", "fiscal_period"] + FUND_COLS
    with cn.cursor() as c:
        c.execute(f"select {', '.join(cols)} from fundamentals where cik = any(%s::int[]) "
                  f"order by cik, period_end", (tableau(ciks),))
        out: dict = {}
        for ligne in c.fetchall():
            r = dict(zip(cols, ligne))
            r["period_end"] = r["period_end"].isoformat()
            out.setdefault(r["cik"], []).append(r)
        return out


def charger_cours(cn, company_ids: list, depuis: date) -> dict:
    """{company_id: [('AAAA-MM-JJ', clôture)]} croissant, déroulé des tableaux mensuels."""
    if not company_ids:
        return {}
    with cn.cursor() as c:
        c.execute("""
            select p.company_id, (p.ym + (t.j - 1))::date, t.c
            from prices p, unnest(p.d, p.c) as t(j, c)
            where p.company_id = any(%s::smallint[]) and p.ym >= date_trunc('month', %s::date)::date
            order by 1, 2
        """, (tableau(company_ids), depuis))
        out: dict = {}
        for cid, d, cl in c.fetchall():
            out.setdefault(cid, []).append((d.isoformat(), float(cl)))
        return out


def ecrire_metriques(cn, company_ids: list, lignes: list, series: list) -> None:
    """
    Met à jour l'instantané et les séries annuelles d'un lot, en DIFFÉRENTIEL.

    La première version supprimait puis réinsérait tout : ~91 000 lignes par soir, alors
    que seuls les multiples bougent avec le cours — les marges, la croissance et la
    dilution restent identiques d'un jour à l'autre. Désormais :
      - une métrique inchangée n'est pas réécrite ;
      - une métrique modifiée est mise à jour ;
      - seule une métrique qui n'est plus produite est supprimée.
    Toujours dans une transaction : relancer le job laisse la base identique, et une
    société absente du lot garde sa dernière valeur connue.

    `lignes` : (company_id, as_of, metric_id, value, raison)
    `series` : (company_id, metric_id, period_end, value)
    """
    if not company_ids:
        return
    with cn.cursor() as c:
        c.execute("create temp table if not exists _met_stage (company_id smallint, as_of date, "
                  "metric_id text, value double precision, raison text, is_applicable boolean) "
                  "on commit drop")
        c.execute("create temp table if not exists _ser_stage (company_id smallint, metric_id text, "
                  "period_end date, value double precision) on commit drop")
        c.execute("create temp table if not exists _lot_ids (company_id smallint) on commit drop")
        c.execute("truncate _met_stage, _ser_stage, _lot_ids")
        copier(c, "_lot_ids", ["company_id"], [(i,) for i in company_ids])
        copier(c, "_met_stage", ["company_id", "as_of", "metric_id", "value", "raison", "is_applicable"],
               [(cid, a, m, _clean(v), r, v is not None or r is None) for cid, a, m, v, r in lignes])
        copier(c, "_ser_stage", ["company_id", "metric_id", "period_end", "value"],
               [(cid, m, p, _clean(v)) for cid, m, p, v in series if _clean(v) is not None])

        c.execute("""
            insert into metrics (company_id, as_of, metric_id, value, raison, is_applicable)
            select company_id, as_of, metric_id, value, raison, is_applicable from _met_stage
            on conflict (company_id, metric_id) do update
              set value = excluded.value, raison = excluded.raison,
                  is_applicable = excluded.is_applicable, as_of = excluded.as_of
              where (metrics.value, metrics.raison) is distinct from (excluded.value, excluded.raison)
        """)
        # Seules disparaissent les métriques qui ne sont plus produites pour la société.
        c.execute("""
            delete from metrics m using _lot_ids l
            where m.company_id = l.company_id
              and not exists (select 1 from _met_stage s
                              where s.company_id = m.company_id and s.metric_id = m.metric_id)
        """)
        c.execute("""
            insert into metric_series (company_id, metric_id, period_end, value)
            select company_id, metric_id, period_end, value from _ser_stage
            on conflict (company_id, metric_id, period_end) do update set value = excluded.value
              where metric_series.value is distinct from excluded.value
        """)
        c.execute("""
            delete from metric_series s using _lot_ids l
            where s.company_id = l.company_id
              and not exists (select 1 from _ser_stage t where t.company_id = s.company_id
                              and t.metric_id = s.metric_id and t.period_end = s.period_end)
        """)


def dernier_depot_ingere(cn, ciks: list) -> dict:
    """
    {cik: date du dépôt le plus récent déjà en base}, pour un lot.

    Borné au lot par l'index de la clé primaire (cik en tête) : ne dépend pas de la
    taille de la table. Sert à ne pas retraiter une société qui n'a rien déposé depuis
    le dernier passage.
    """
    if not ciks:
        return {}
    with cn.cursor() as c:
        c.execute("select cik, max(filed) from fundamentals where cik = any(%s::int[]) group by cik",
                  (tableau(ciks),))
        return dict(c.fetchall())
