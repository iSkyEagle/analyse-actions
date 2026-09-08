"""Test d'intégration : providers réels -> base réelle, puis rejeu pour l'idempotence."""
import logging, os, sys
from datetime import date, timedelta
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
os.environ.setdefault("SEC_USER_AGENT", "Analyse Actions contact@exemple.fr")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres@/stocks?host=/tmp&port=5433")

import db
from providers.base import Issuer, OutOfScope, ProviderError
from providers.edgar import EdgarApiProvider
from providers.prices import YFinanceProvider

CIBLES = [("MSFT", 789019, 7372), ("CRBU", 1619856, 2836), ("JPM", 19617, 6021),
          ("SPOT", 1639920, 7372)]   # SPOT = deposant IFRS, doit lever OutOfScope

cn = db.connect()
with cn.cursor() as c:
    c.execute("truncate companies restart identity cascade; truncate fundamentals; "
              "truncate ingestion_runs restart identity cascade;")
cn.commit()

edgar = EdgarApiProvider()
issuers = [Issuer(cik=k, ticker=t, name=t, exchange="Nasdaq", sic=s) for t, k, s in CIBLES]

print("=== list_issuers (univers complet) ===")
univ = list(edgar.list_issuers())
print(f"  {len(univ)} lignes de cotation sur Nasdaq/NYSE/CBOE")

print("\n=== upsert_companies ===")
ids = db.upsert_companies(cn, issuers); cn.commit()
print("  company_id attribués :", {t: ids[t] for t, _, _ in CIBLES})

print("\n=== get_fundamentals + journal ===")
with db.run(cn, "test_fundamentals", edgar.name) as r:
    for iss in issuers:
        try:
            res = edgar.get_fundamentals(iss)
        except OutOfScope as e:
            r.fail_provider(e, ticker=iss.ticker, cik=iss.cik)
            print(f"  {iss.ticker:6} ECARTE  [{e.profile}] {e.reason[:52]}")
            continue
        except ProviderError as e:
            r.fail_provider(e, ticker=iss.ticker, cik=iss.cik)
            print(f"  {iss.ticker:6} ECHEC   {e}")
            continue
        n = db.upsert_fundamentals(cn, res.rows)
        db.update_company_facts(cn, res, sic=iss.sic)
        r.ok()
        print(f"  {iss.ticker:6} OK  profil={res.mapping_profile:10} {n:4} lignes  "
              f"dqs={res.data_quality_score:.2f}  {res.fiscal_years} exercices")
        cn.commit()

print("\n=== YFinanceProvider : appels groupes uniquement ===")
yfp = YFinanceProvider()
ok_t = [t for t, _, _ in CIBLES]
with db.run(cn, "test_prices", yfp.name) as r:
    for s in yfp.get_price_history(ok_t, date.today() - timedelta(days=400), date.today()):
        if not s.ok:
            r.fail(ticker=s.ticker, stage="fetch", reason=s.reason); continue
        n = db.upsert_prices(cn, ids[s.ticker], s); r.ok()
        print(f"  {s.ticker:6} {len(s.days):4} seances -> {n} mois")
    cn.commit()

print("\n=== capitalisation depuis EDGAR ===")
n = db.refresh_market_caps(cn); cn.commit()
with cn.cursor() as c:
    c.execute("select ticker, round((market_cap/1e9)::numeric,1), market_cap_uncertain, "
              "market_cap_as_of from companies where market_cap is not null order by 2 desc")
    for row in c.fetchall():
        print(f"  {row[0]:6} {row[1]:>9} Md$  incertaine={row[2]}  au {row[3]}")

print("\n=== rejeu integral : idempotence ===")
with cn.cursor() as c:
    c.execute("select (select count(*) from fundamentals),(select count(*) from prices),"
              "(select count(*) from prices_daily)")
    before = c.fetchone()
for iss in issuers:
    try:
        res = edgar.get_fundamentals(iss)
    except ProviderError:
        continue
    db.upsert_fundamentals(cn, res.rows)
for s in yfp.get_price_history(ok_t, date.today() - timedelta(days=400), date.today()):
    if s.ok: db.upsert_prices(cn, ids[s.ticker], s)
cn.commit()
with cn.cursor() as c:
    c.execute("select (select count(*) from fundamentals),(select count(*) from prices),"
              "(select count(*) from prices_daily)")
    after = c.fetchone()
print(f"  avant {before}\n  apres {after}   -> {'IDEMPOTENT' if before==after else 'BUG'}")

print("\n=== journal d'ingestion ===")
with cn.cursor() as c:
    c.execute("""select r.job, r.n_ok, r.n_failed, e.ticker, e.stage, e.reason, left(e.detail,60)
                 from ingestion_runs r left join ingestion_errors e using (run_id) order by r.run_id""")
    for row in c.fetchall():
        print("  ", " | ".join("" if x is None else str(x) for x in row))
edgar.close(); cn.close()
