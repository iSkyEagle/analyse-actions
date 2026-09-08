"""Test d'intégration de universe.py : passe A complète, passe B sur échantillon."""
import logging, os, random, time
from datetime import date, timedelta

logging.basicConfig(level=logging.INFO, format="%(message)s")
os.environ.setdefault("SEC_USER_AGENT", "Analyse Actions contact@exemple.fr")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres@/stocks?host=/tmp&port=5433")

import psycopg2.extras as X
import db, universe as U
from providers.base import ProviderError
from providers.edgar import EdgarApiProvider
from providers.prices import YFinanceProvider

edgar = EdgarApiProvider()

print("=== PASSE A — EDGAR seul ===")
filings = U.RecentFilings().load(edgar, cache_dir="data")
cands, funnel = U.build_candidates(edgar, filings)
prev = None
for k, v in funnel.items():
    print(f"  {k:38} {v:>6}" + (f"  ({v - prev:+d})" if prev else ""))
    prev = v
print(f"  candidats : {len(cands)} lignes / {len({i.cik for i in cands})} CIK")

cn = db.connect()
with cn.cursor() as c:
    c.execute("truncate companies restart identity cascade; truncate fundamentals; "
              "truncate ingestion_runs restart identity cascade;")
cn.commit()

with db.run(cn, "universe_A", "form.idx") as r:
    ids = db.upsert_companies(cn, cands)
    with cn.cursor() as c:
        X.execute_values(c, "update companies set last_xbrl_filing = v.d::date "
                            "from (values %s) as v(t, d) where companies.ticker = v.t",
                         [(i.ticker, filings.last[i.cik][0]) for i in cands], page_size=1000)
    r.ok(len(cands))
    cn.commit()
print(f"  {len(ids)} lignes en base")

print("\n=== enrichissement SIC (150 candidats, mesure de debit) ===")
import time as _t; _t0=_t.time()
random.seed(11); _s = random.sample(cands, 300)
with db.run(cn, "sic_sample", edgar.name) as r:
    n = U.enrich_with_sic(edgar, _s, run=r, limit=150)
_dt=_t.time()-_t0
print(f"  {n}/150 SIC en {_dt:.0f} s -> {_dt/150*4816/60:.1f} min projetees pour 4816")
import collections as _c
print("  profils:", dict(_c.Counter(__import__('mapping').profile_from_sic(i.sic) for i in _s[:150])))
with cn.cursor() as c:
    X.execute_values(c, "update companies set sic = v.s::smallint, mapping_profile = v.p::mapping_profile_t "
                        "from (values %s) as v(t,s,p) where companies.ticker = v.t",
                     [(i.ticker, i.sic, __import__('mapping').profile_from_sic(i.sic))
                      for i in _s[:150] if i.sic], page_size=500)
cn.commit()

print("\n=== PASSE B — cours, échantillon de 300 tickers ===")
sample = _s
yfp = YFinanceProvider()
t0 = time.time()
with db.run(cn, "prices_sample", yfp.name) as r:
    got = 0
    for s in yfp.get_price_history([i.ticker for i in sample],
                                   date.today() - timedelta(days=100), date.today()):
        if not s.ok:
            r.fail(ticker=s.ticker, stage="fetch", reason=s.reason)
            continue
        db.upsert_prices(cn, ids[s.ticker], s)
        r.ok()
        got += 1
    cn.commit()
dt = time.time() - t0
print(f"  {got}/300 séries en {dt:.0f} s  ->  {dt / 300 * 4802 / 60:.1f} min projetées pour 4802")

print("\n=== actions EDGAR, 60 tickers ===")
with db.run(cn, "shares_sample", edgar.name) as r:
    for i in sample[:60]:
        try:
            res = edgar.get_fundamentals(i)
        except ProviderError as e:
            r.fail_provider(e, ticker=i.ticker, cik=i.cik)
            continue
        db.upsert_fundamentals(cn, res.rows)
        db.update_company_facts(cn, res, sic=i.sic)
        r.ok()
    cn.commit()
print(f"  {db.refresh_market_caps(cn)} capitalisations calculées")
cn.commit()

print("\n=== finalize_universe ===")
for k, v in U.finalize_universe(cn).items():
    print(f"  {k:20} {v}")

with cn.cursor() as c:
    c.execute("""select ticker, round((market_cap/1e6)::numeric, 0),
                        round((median_dollar_volume_3m/1e3)::numeric, 0),
                        in_universe, history_years
                 from companies where market_cap is not null
                 order by market_cap desc limit 6""")
    print(f"\n  {'tk':8}{'capi M$':>10}{'vol k$/j':>11}{'retenu':>9}{'ans':>5}")
    for row in c.fetchall():
        print(f"  {row[0]:8}{row[1]:>10}{row[2]:>11}{str(row[3]):>9}{row[4]:>5}")
    c.execute("""select ticker, round((market_cap/1e6)::numeric, 1),
                        round((median_dollar_volume_3m/1e3)::numeric, 1)
                 from companies where market_cap is not null and not in_universe
                 order by market_cap limit 5""")
    print("\n  écartés :")
    for row in c.fetchall():
        print(f"  {row[0]:8}{row[1]:>10} M$ {row[2]:>10} k$/j")
    c.execute("select stage, reason, count(*) from ingestion_errors group by 1,2 order by 3 desc limit 5")
    rows = c.fetchall()
    print("\n  journal des échecs :", rows or "aucun")

edgar.close()
cn.close()
