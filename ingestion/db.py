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
        # D'abord sous un point de sauvegarde : un échec d'écriture du journal ne doit
        # pas annuler le travail en cours. Ensuite seulement, si la transaction est déjà
        # avortée, le rollback global — qui purge cet état et permet d'écrire la trace.
        try:
            with self.cn.cursor() as c:
                c.execute("savepoint journal")
                c.execute(sql, args)
                c.execute("release savepoint journal")
            return
        except Exception:
            try:
                with self.cn.cursor() as c:
                    c.execute("rollback to savepoint journal")
            except Exception:
                self.cn.rollback()
        try:
            with self.cn.cursor() as c:
                c.execute(sql, args)
        except Exception as e:
            self.cn.rollback()
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
        c.execute("create temp table if not exists _cmp_stage (cik int, ticker text, name text, "
                  "exchange text, mapping_profile text) on commit drop")
        c.execute("truncate _cmp_stage")
        copier(c, "_cmp_stage", ["cik", "ticker", "name", "exchange", "mapping_profile"], rows)
        # `updated_at` exclu de la comparaison : c'est lui qui rendait chaque ligne
        # « différente », et les 4 816 lignes étaient réécrites chaque mois.
        c.execute("""
            insert into companies (cik, ticker, name, exchange, mapping_profile)
            select cik, ticker, name, exchange, mapping_profile::mapping_profile_t from _cmp_stage
            on conflict (ticker) do update
              set cik = excluded.cik, name = excluded.name,
                  exchange = excluded.exchange,
                  -- Immobilier 6500-6599 : le profil déduit du seul SIC est provisoire.
                  -- Celui fixé par le passage des comptes (foncière ou standard) est
                  -- conservé ; sans cette exception, l'univers le réécraserait chaque mois.
                  mapping_profile = case
                    when companies.sic between 6500 and 6599
                     and companies.mapping_profile::text in ('reit', 'standard')
                     and excluded.mapping_profile::text = 'reit'
                    then companies.mapping_profile
                    else excluded.mapping_profile end,
                  updated_at = now()
              where (companies.cik, companies.name, companies.exchange)
                    is distinct from (excluded.cik, excluded.name, excluded.exchange)
                 or (companies.mapping_profile is distinct from excluded.mapping_profile
                     and not (companies.sic between 6500 and 6599
                              and companies.mapping_profile::text in ('reit', 'standard')
                              and excluded.mapping_profile::text = 'reit'))
        """)
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
            -- CIK nul exclu, et `exists` plutôt que `in` : les indices de référence n'ont
            -- pas de CIK, et `x in (liste contenant NULL)` vaut NULL — pas faux — pour
            -- toute valeur absente de la liste. Le recalcul plantait alors sur la
            -- contrainte non nulle de market_cap_uncertain, pour TOUTES les sociétés.
            multi as (select cik from companies where cik is not null group by cik having count(*) > 1)
            update companies c set
              market_cap = c.shares_outstanding * d.close_adj,
              market_cap_as_of = d.dt,
              market_cap_uncertain = exists (select 1 from multi m where m.cik = c.cik),
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
_DONNEES = ["fiscal_year"] + FUND_COLS + ["accn", "filed", "inferred_zero", "q4_derived", "scale_corrected",
                                         "first_filed", "shares_filed", "ytd_derived"]
_CHANGE = (f"({', '.join('fundamentals.' + c for c in _DONNEES)}) is distinct from "
           f"({', '.join('excluded.' + c for c in _DONNEES)})")

_COLS_ECRITES = ["cik", "period_end", "fiscal_period", "fiscal_year"] + FUND_COLS + [
    "accn", "filed", "inferred_zero", "q4_derived", "scale_corrected",
    "first_filed", "shares_filed", "ytd_derived"]

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
  first_filed = excluded.first_filed,
  shares_filed = excluded.shares_filed,
  ytd_derived = excluded.ytd_derived,
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
                 list(getattr(r, "scale_corrected", None) or []),
                 r.first_filed, r.shares_filed, list(r.ytd_derived or [])])
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
        if force:
            # Mode forcé : l'extraction d'une société est la vérité complète sur ses
            # comptes. Les périodes qu'elle ne produit plus sont supprimées — les 55
            # exercices fantômes d'Amazon, tirés de données glissantes sur douze mois,
            # resteraient sinon en base après correction de l'extraction. Jamais hors
            # mode forcé, et jamais sur une extraction vide (retour anticipé plus haut).
            c.execute("""
                delete from fundamentals f
                using (select distinct cik from _fund_stage) s
                where f.cik = s.cik
                  and not exists (select 1 from _fund_stage t
                                  where t.cik = f.cik and t.period_end = f.period_end
                                    and t.fiscal_period = f.fiscal_period)
            """)
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
    cols = ["cik", "period_end", "fiscal_period"] + FUND_COLS + ["filed", "first_filed", "shares_filed"]
    with cn.cursor() as c:
        c.execute(f"select {', '.join(cols)} from fundamentals where cik = any(%s::int[]) "
                  f"order by cik, period_end", (tableau(ciks),))
        out: dict = {}
        for ligne in c.fetchall():
            r = dict(zip(cols, ligne))
            for k in ("period_end", "filed", "first_filed", "shares_filed"):
                if r[k] is not None:
                    r[k] = r[k].isoformat()
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


# =============================================================================
# Étape 4b — reconstruction des cours, divisions, dividendes, indices
# =============================================================================
INDICES = [("^GSPC", "S&P 500"), ("^SP500TR", "S&P 500, dividendes réinvestis")]


def assurer_indices(cn) -> None:
    """Crée les lignes des indices de référence si elles n'existent pas."""
    with cn.cursor() as c:
        for ticker, nom in INDICES:
            c.execute("""
                insert into companies (ticker, name, exchange, mapping_profile, history_years, in_universe)
                values (%s, %s, 'INDEX', 'index', 10, false)
                on conflict (ticker) do nothing
            """, (ticker, nom))


def lignes_indices(cn) -> list:
    """Même forme que select_universe : les jobs de cours les traitent comme une valeur."""
    with cn.cursor() as c:
        c.execute("select company_id, cik, ticker, sic, mapping_profile::text, history_years "
                  "from companies where mapping_profile::text = 'index' order by company_id")
        return c.fetchall()


def a_reconstruire(cn, company_ids: list) -> set:
    """Sociétés dont l'historique n'est pas encore sur la base « ajustée des divisions »."""
    if not company_ids:
        return set()
    with cn.cursor() as c:
        c.execute("select company_id from companies where company_id = any(%s::smallint[]) "
                  "and prices_rebuilt_at is null", (tableau(company_ids),))
        return {r[0] for r in c.fetchall()}


def _lot(c, nom: str, company_ids) -> None:
    c.execute(f"create temp table if not exists {nom} (company_id smallint) on commit drop")
    c.execute(f"truncate {nom}")
    copier(c, nom, ["company_id"], [(i,) for i in company_ids])


def remplacer_cours(cn, company_ids: list, mois: list) -> int:
    """
    Remplace TOUT l'historique de cours des sociétés données, et les date reconstruites.

    Jamais de fusion ici : fusionner une série ajustée des seules divisions dans un
    historique ajusté des dividendes fabriquerait une série hybride, avec une marche à
    chaque frontière — le défaut déjà vu à l'étape 4a. Une société dont la série
    téléchargée est vide n'est pas touchée : on ne remplace pas un historique par rien.
    """
    ids = sorted({m[0] for m in mois} & set(company_ids))
    if not ids:
        return 0
    with cn.cursor() as c:
        _lot(c, "_lot_cours", ids)
        c.execute("create temp table if not exists _px_remplace "
                  "(company_id smallint, ym date, d smallint[], c real[], v real[]) on commit drop")
        c.execute("truncate _px_remplace")
        copier(c, "_px_remplace", ["company_id", "ym", "d", "c", "v"], mois)
        c.execute("delete from prices p using _lot_cours l where p.company_id = l.company_id")
        c.execute("insert into prices (company_id, ym, d, c, v) "
                  "select company_id, ym, d, c, v from _px_remplace")
        c.execute("update companies set prices_rebuilt_at = current_date "
                  "from _lot_cours l where companies.company_id = l.company_id")
    return len(mois)


def ecrire_evenements(cn, divisions: list, dividendes: list, remplaces: list) -> set:
    """
    Enregistre divisions et dividendes. Renvoie les sociétés dont une division était
    INCONNUE jusqu'ici, hors sociétés reconstruites dans ce même lot.

    Une nouvelle division réajuste rétroactivement tout l'historique chez Yahoo : la
    fusionner avec l'historique stocké produirait une falaise. L'appelant ne doit donc
    pas écrire les cours de ces sociétés, qui sont marquées à reconstruire.

    `divisions` : (company_id, date, ratio) ; `dividendes` : (company_id, date, montant)
    `remplaces` : sociétés reconstruites, dont les événements sont remplacés en entier.
    """
    with cn.cursor() as c:
        c.execute("create temp table if not exists _evt_div (company_id smallint, date date, ratio real) on commit drop")
        c.execute("create temp table if not exists _evt_dvd (company_id smallint, date date, amount real) on commit drop")
        c.execute("truncate _evt_div, _evt_dvd")
        copier(c, "_evt_div", ["company_id", "date", "ratio"], divisions)
        copier(c, "_evt_dvd", ["company_id", "date", "amount"], dividendes)
        if remplaces:
            _lot(c, "_lot_evt", remplaces)
            c.execute("delete from splits s using _lot_evt l where s.company_id = l.company_id")
            c.execute("delete from dividends d using _lot_evt l where d.company_id = l.company_id")
        c.execute("""
            with nouvelles as (
                insert into splits (company_id, date, ratio)
                select company_id, date, ratio from _evt_div
                on conflict (company_id, date) do nothing
                returning company_id
            )
            select distinct company_id from nouvelles
        """)
        nouvelles = {r[0] for r in c.fetchall()} - set(remplaces)
        c.execute("""
            insert into dividends (company_id, date, amount)
            select company_id, date, amount from _evt_dvd
            on conflict (company_id, date) do update set amount = excluded.amount
              where dividends.amount is distinct from excluded.amount
        """)
        if nouvelles:
            c.execute("update companies set prices_rebuilt_at = null "
                      "where company_id = any(%s::smallint[])", (tableau(nouvelles),))
    return nouvelles


# =============================================================================
# Étape 4c — divisions, dividendes, indice, résumés historiques
# =============================================================================
def charger_divisions(cn, company_ids: list) -> dict:
    """{company_id: [('AAAA-MM-JJ', ratio)]} — tout l'historique : un dépôt ancien peut
    précéder une division vieille de dix ans."""
    if not company_ids:
        return {}
    with cn.cursor() as c:
        c.execute("select company_id, date, ratio from splits where company_id = any(%s::smallint[]) "
                  "order by 1, 2", (tableau(company_ids),))
        out: dict = {}
        for cid, d, r in c.fetchall():
            out.setdefault(cid, []).append((d.isoformat(), float(r)))
        return out


def charger_dividendes(cn, company_ids: list, depuis: date) -> dict:
    """{company_id: [('AAAA-MM-JJ', montant)]} sur la fenêtre de cours."""
    if not company_ids:
        return {}
    with cn.cursor() as c:
        c.execute("select company_id, date, amount from dividends where company_id = any(%s::smallint[]) "
                  "and date >= %s order by 1, 2", (tableau(company_ids), depuis))
        out: dict = {}
        for cid, d, x in c.fetchall():
            out.setdefault(cid, []).append((d.isoformat(), float(x)))
        return out


def id_indice(cn, ticker: str = "^SP500TR"):
    with cn.cursor() as c:
        c.execute("select company_id from companies where ticker = %s", (ticker,))
        r = c.fetchone()
        return r[0] if r else None


def ecrire_resumes(cn, company_ids: list, lignes: list) -> None:
    """
    Remplace les résumés historiques d'un lot, en différentiel.
    `lignes` : (company_id, metric_id, as_of, n, couverture, p10, mediane, p90, mini, maxi)
    """
    if not company_ids:
        return
    cols = ["company_id", "metric_id", "as_of", "n", "couverture", "p10", "mediane", "p90", "mini", "maxi"]
    with cn.cursor() as c:
        _lot(c, "_lot_hist", company_ids)
        c.execute("create temp table if not exists _hist_stage (company_id smallint, metric_id text, "
                  "as_of date, n int, couverture real, p10 float8, mediane float8, p90 float8, "
                  "mini float8, maxi float8) on commit drop")
        c.execute("truncate _hist_stage")
        copier(c, "_hist_stage", cols, [tuple(_clean(v) if isinstance(v, float) else v for v in l)
                                        for l in lignes])
        c.execute(f"""
            insert into metric_history ({', '.join(cols)})
            select {', '.join(cols)} from _hist_stage
            on conflict (company_id, metric_id) do update set
              as_of = excluded.as_of, n = excluded.n, couverture = excluded.couverture,
              p10 = excluded.p10, mediane = excluded.mediane, p90 = excluded.p90,
              mini = excluded.mini, maxi = excluded.maxi
              where (metric_history.n, metric_history.p10, metric_history.mediane, metric_history.p90,
                     metric_history.mini, metric_history.maxi)
                    is distinct from (excluded.n, excluded.p10, excluded.mediane, excluded.p90,
                                      excluded.mini, excluded.maxi)
        """)
        c.execute("""
            delete from metric_history h using _lot_hist l
            where h.company_id = l.company_id
              and not exists (select 1 from _hist_stage s
                              where s.company_id = h.company_id and s.metric_id = h.metric_id)
        """)


def charger_resumes(cn, company_ids: list) -> dict:
    """{company_id: {metric_id: résumé}} — les résumés hebdomadaires, pour le verdict."""
    if not company_ids:
        return {}
    with cn.cursor() as c:
        c.execute("select company_id, metric_id, couverture, p10, mediane, p90 from metric_history "
                  "where company_id = any(%s::smallint[])", (tableau(company_ids),))
        out: dict = {}
        for cid, mid, cv, p10, med, p90 in c.fetchall():
            out.setdefault(cid, {})[mid] = {"couverture": cv, "p10": p10, "mediane": med, "p90": p90}
        return out


def appliquer_secteurs(cn) -> int:
    """
    Classe toutes les sociétés (secteurs.py) et écrit secteur et industrie.

    Ne réécrit que ce qui change : un passage sans modification de la taxonomie ni des
    codes SIC n'écrit rien. Les indices de référence n'ont pas de secteur.
    Renvoie le nombre de sociétés dont la classification a changé.
    """
    import secteurs as S
    with cn.cursor() as c:
        c.execute("select company_id, cik, sic, mapping_profile::text from companies "
                  "where mapping_profile::text <> 'index'")
        lignes = [(cid, *S.classer(sic, cik, profil)) for cid, cik, sic, profil in c.fetchall()]
        c.execute("create temp table if not exists _sect_stage (company_id smallint, sector text, "
                  "industry text) on commit drop")
        c.execute("truncate _sect_stage")
        copier(c, "_sect_stage", ["company_id", "sector", "industry"], lignes)
        c.execute("""
            update companies c set sector = s.sector, industry = s.industry, updated_at = now()
            from _sect_stage s
            where c.company_id = s.company_id
              and (c.sector, c.industry) is distinct from (s.sector, s.industry)
        """)
        return c.rowcount


def ecrire_profil(cn, cik: int, profil: str) -> None:
    """Profil seul, pour une société sans dépôt nouveau dont le profil a changé."""
    with cn.cursor() as c:
        c.execute("update companies set mapping_profile = %s::mapping_profile_t, updated_at = now() "
                  "where cik = %s and mapping_profile::text is distinct from %s", (profil, cik, profil))


# =============================================================================
# Étape 3b — percentiles sectoriels
# =============================================================================
def charger_pour_percentiles(cn) -> list:
    """
    Toutes les métriques chiffrées, avec secteur, industrie et appartenance à l'univers.

    Lecture de toute la table `metrics` : inhérent à un percentile, qui compare chaque
    société à toutes les autres. Son volume est borné par la taille de l'univers — une
    ligne par société et par métrique —, jamais par la profondeur de l'historique.
    """
    with cn.cursor() as c:
        c.execute("""
            select m.company_id, m.metric_id, m.value, co.sector, co.industry, co.in_universe
            from metrics m join companies co using (company_id)
            where m.value is not null and m.metric_id not like '\\_%%'
        """)
        return c.fetchall()


def ecrire_percentiles(cn, positions: list, stats: list) -> int:
    """Remplace percentiles et distributions, en différentiel. Renvoie les lignes modifiées."""
    aujourdhui = date.today()
    with cn.cursor() as c:
        c.execute("create temp table if not exists _pct_stage (company_id smallint, metric_id text, "
                  "percentile smallint, niveau text, n smallint) on commit drop")
        c.execute("create temp table if not exists _gst_stage (niveau text, groupe text, metric_id text, "
                  "n int, p10 float8, p25 float8, mediane float8, p75 float8, p90 float8) on commit drop")
        c.execute("truncate _pct_stage, _gst_stage")
        copier(c, "_pct_stage", ["company_id", "metric_id", "percentile", "niveau", "n"], positions)
        copier(c, "_gst_stage", ["niveau", "groupe", "metric_id", "n", "p10", "p25", "mediane", "p75", "p90"],
               [tuple(_clean(x) if isinstance(x, float) else x for x in s) for s in stats])
        c.execute("""
            update metrics m set percentile = s.percentile, percentile_niveau = s.niveau,
                                 percentile_n = s.n
            from _pct_stage s
            where m.company_id = s.company_id and m.metric_id = s.metric_id
              and (m.percentile, m.percentile_niveau, m.percentile_n)
                  is distinct from (s.percentile, s.niveau, s.n)
        """)
        n = c.rowcount
        # Une métrique devenue non applicable, ou dont le groupe est passé sous 15, perd
        # son percentile au lieu de garder un rang périmé.
        c.execute("""
            update metrics m set percentile = null, percentile_niveau = null, percentile_n = null
            where m.percentile is not null
              and not exists (select 1 from _pct_stage s
                              where s.company_id = m.company_id and s.metric_id = m.metric_id)
        """)
        n += c.rowcount
        c.execute("""
            insert into groupe_stats (niveau, groupe, metric_id, as_of, n, p10, p25, mediane, p75, p90)
            select niveau, groupe, metric_id, %s, n, p10, p25, mediane, p75, p90 from _gst_stage
            on conflict (niveau, groupe, metric_id) do update set
              as_of = excluded.as_of, n = excluded.n, p10 = excluded.p10, p25 = excluded.p25,
              mediane = excluded.mediane, p75 = excluded.p75, p90 = excluded.p90
              where (groupe_stats.n, groupe_stats.p10, groupe_stats.p25, groupe_stats.mediane,
                     groupe_stats.p75, groupe_stats.p90)
                    is distinct from (excluded.n, excluded.p10, excluded.p25, excluded.mediane,
                                      excluded.p75, excluded.p90)
        """, (aujourdhui,))
        c.execute("""
            delete from groupe_stats g
            where not exists (select 1 from _gst_stage s where s.niveau = g.niveau
                              and s.groupe = g.groupe and s.metric_id = g.metric_id)
        """)
    return n


# =============================================================================
# Étape 3c — notes
# =============================================================================
def charger_pour_notes(cn) -> dict:
    """
    {company_id: (profil, z du verdict, {metric_id: raison des valeurs absentes})}.

    Seulement les sociétés qui ont au moins une métrique chiffrée : les autres — hors
    univers, jamais calculées — n'ont rien à noter. Sans ce filtre, chacune recevait cinq
    lignes vides (5 766 lignes sur 19 697 en octobre 2026).
    """
    with cn.cursor() as c:
        c.execute("""
            select co.company_id, co.mapping_profile::text, v.value
            from companies co
            left join metrics v on v.company_id = co.company_id and v.metric_id = '_verdict_valorisation'
            where co.mapping_profile::text in ('standard', 'financial', 'reit')
              and exists (select 1 from metrics m
                          where m.company_id = co.company_id and m.value is not null
                            and m.metric_id not like '\\_%%')
        """)
        out = {cid: (prof, z, {}) for cid, prof, z in c.fetchall()}
        # Les raisons servent à distinguer le non-interprétable du manquant (règle 2).
        c.execute("""
            select company_id, metric_id, raison from metrics
            where value is null and raison is not null and metric_id not like '\\_%%'
        """)
        for cid, mid, raison in c.fetchall():
            if cid in out:
                out[cid][2][mid] = raison
        return out


def ecrire_notes(cn, lignes: list) -> int:
    """
    Remplace les notes, en différentiel. `lignes` : (company_id, axe, score 0-100 | None,
    couverture_pct, n). Une note globale absente (moins de trois axes) n'a pas de ligne.
    """
    aujourdhui = date.today()
    with cn.cursor() as c:
        c.execute("create temp table if not exists _note_stage (company_id smallint, axis text, "
                  "score smallint, coverage_pct smallint, n_applicable smallint) on commit drop")
        c.execute("truncate _note_stage")
        copier(c, "_note_stage", ["company_id", "axis", "score", "coverage_pct", "n_applicable"], lignes)
        c.execute("""
            insert into scores (company_id, as_of, axis, score, coverage_pct, n_applicable)
            select company_id, %s, axis::score_axis_t, score, coverage_pct, n_applicable from _note_stage
            on conflict (company_id, axis) do update set
              as_of = excluded.as_of, score = excluded.score, coverage_pct = excluded.coverage_pct,
              n_applicable = excluded.n_applicable
              where (scores.score, scores.coverage_pct, scores.n_applicable)
                    is distinct from (excluded.score, excluded.coverage_pct, excluded.n_applicable)
        """, (aujourdhui,))
        n = c.rowcount
        c.execute("""
            delete from scores s
            where not exists (select 1 from _note_stage t
                              where t.company_id = s.company_id and t.axis = s.axis::text)
        """)
        return n + c.rowcount
