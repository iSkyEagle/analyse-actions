-- =============================================================================
-- 0001_core.sql — Schéma central de la phase 1
--
-- Trois tables portent la donnée brute : companies, prices, fundamentals.
-- Deux tables portent l'exploitabilité du pipeline : ingestion_runs, ingestion_errors.
-- Les tables d'analyse (metrics, sector_stats, scores, insights) sont en 0002.
--
-- Écarts assumés par rapport au §5 du mémoire, tous issus des étapes 1 et 2 :
--   * `prices` stocke un mois par ligne en tableaux, pas un jour  (110 Mo au lieu de 629)
--   * `companies` est clé par company_id smallint, pas par ticker (2 octets contre 9,
--     répété 7 millions de fois dans prices)
--   * `fundamentals` reste clé par CIK : deux lignes de cotation d'un même émetteur
--     (GOOGL/GOOG, BRK-A/BRK-B) partagent les mêmes comptes
--   * ajout de accn, filed, inferred_zero, q4_derived pour la traçabilité
-- =============================================================================

create type fiscal_period_t as enum ('Q', 'FY');

-- 'ifrs' et 'foreign' : déposants annuels uniquement (20-F / 40-F), hors périmètre
-- fondamentaux en phase 1. Leurs cours sont ingérés, leurs comptes restent vides.
create type mapping_profile_t as enum (
    'standard', 'financial', 'reit',
    'ifrs', 'foreign', 'fund', 'spac'
);


-- =============================================================================
-- companies — une ligne par ligne de cotation, pas par émetteur
-- =============================================================================
create table companies (
    company_id      smallint generated always as identity primary key,
    cik             integer not null,
    ticker          text    not null unique,
    name            text    not null,
    exchange        text    not null,          -- Nasdaq | NYSE | CBOE
    sector          text,
    industry        text,
    sic             smallint,
    mapping_profile mapping_profile_t not null default 'standard',

    -- Capitalisation : jamais lue chez un fournisseur de cours.
    -- shares_outstanding vient de dei:EntityCommonStockSharesOutstanding (EDGAR),
    -- market_cap = shares_outstanding × dernier close, recalculé à chaque ingestion.
    shares_outstanding   double precision,
    shares_as_of         date,
    market_cap           double precision,
    market_cap_as_of     date,
    market_cap_uncertain boolean not null default false,   -- émetteur multi-classes

    median_dollar_volume_3m double precision,
    last_xbrl_filing        date,
    data_quality_score      real,
    fiscal_years_available  smallint,

    in_universe  boolean not null default false,   -- a passé les filtres d'ingestion
    history_years smallint not null default 5,     -- profondeur de cours visée
    first_seen   date        not null default current_date,
    updated_at   timestamptz not null default now(),

    constraint companies_dqs_range check (data_quality_score between 0 and 1)
);

create index companies_cik_idx     on companies (cik);
create index companies_universe_idx on companies (mapping_profile) where in_universe;
create index companies_mcap_idx    on companies (market_cap desc) where in_universe;

comment on column companies.market_cap_uncertain is
  'companyfacts supprime les dimensions : sur un émetteur multi-classes, le nombre '
  'd''actions de la page de garde peut ne couvrir qu''une classe. Écart > 20 % avec '
  'les actions moyennes de base => capitalisation marquée incertaine, jamais masquée.';


-- =============================================================================
-- prices — un mois de cotation par ligne, en tableaux alignés
--
-- Mesuré sur 7,18 M jours : 629 Mo en ligne-par-jour naïve, 457 Mo en types resserrés,
-- 110 Mo ici. L'en-tête de ligne Postgres (24 octets) est amorti sur ~21 valeurs et
-- l'index passe de 7,18 M à 334 500 entrées.
-- Le seul schéma d'accès réel est la lecture d'une série entière : 12 blocs, 0,4 ms.
-- =============================================================================
create table prices (
    company_id smallint not null references companies (company_id) on delete cascade,
    ym         date     not null,     -- premier jour du mois
    d          smallint[] not null,   -- quantièmes cotés, croissants
    c          real[]     not null,   -- clôture ajustée
    v          real[]     not null,   -- volume en titres

    primary key (company_id, ym),
    constraint prices_is_month check (ym = date_trunc('month', ym)::date),
    constraint prices_aligned check (
        array_length(d, 1) = array_length(c, 1)
        and array_length(d, 1) = array_length(v, 1)
        and array_length(d, 1) between 1 and 31
    )
);

-- Rend le format transparent pour toute requête ad hoc : le stockage est compact,
-- la lecture reste du SQL ordinaire.
create view prices_daily as
select p.company_id,
       (p.ym + (t.day - 1))::date as dt,
       t.close_adj,
       t.volume
from prices p,
     unnest(p.d, p.c, p.v) as t(day, close_adj, volume);

comment on view prices_daily is
  'Vue de confort : déplie les tableaux mensuels en (company_id, dt, close_adj, volume).';


-- Fusion idempotente d'un lot de jours dans le mois courant.
-- Relancer l''ingestion deux fois ne duplique ni ne perd rien : à quantième égal,
-- la nouvelle valeur écrase l''ancienne, le reste du mois est conservé.
create or replace function merge_price_month(
    p_company_id smallint,
    p_ym         date,
    p_d          smallint[],
    p_c          real[],
    p_v          real[]
) returns void language plpgsql as $$
declare
    m_d smallint[]; m_c real[]; m_v real[];
begin
    with ancien as (
        select t.day, t.close_adj, t.volume
        from prices p, unnest(p.d, p.c, p.v) as t(day, close_adj, volume)
        where p.company_id = p_company_id and p.ym = p_ym
    ),
    nouveau as (
        select t.day, t.close_adj, t.volume
        from unnest(p_d, p_c, p_v) as t(day, close_adj, volume)
    ),
    fusion as (
        select coalesce(n.day, a.day) as day,
               coalesce(n.close_adj, a.close_adj) as close_adj,
               coalesce(n.volume, a.volume) as volume
        from ancien a full outer join nouveau n using (day)
        order by 1
    )
    select array_agg(day), array_agg(close_adj), array_agg(volume)
    into m_d, m_c, m_v from fusion;

    insert into prices (company_id, ym, d, c, v)
    values (p_company_id, p_ym, m_d, m_c, m_v)
    on conflict (company_id, ym) do update
        set d = excluded.d, c = excluded.c, v = excluded.v;
end $$;


-- =============================================================================
-- fundamentals — un exercice ou un trimestre par ligne, clé par CIK
-- =============================================================================
create table fundamentals (
    cik           integer          not null,
    period_end    date             not null,
    fiscal_period fiscal_period_t  not null,
    fiscal_year   smallint,

    -- compte de résultat
    revenue           double precision,
    cogs              double precision,
    gross_profit      double precision,
    operating_income  double precision,
    net_income        double precision,
    eps_diluted       double precision,
    shares_diluted    double precision,
    shares_basic      double precision,
    sbc               double precision,
    d_and_a           double precision,
    interest_expense  double precision,
    income_tax        double precision,
    rd_expense        double precision,
    ebitda            double precision,

    -- bilan
    total_assets        double precision,
    total_liabilities   double precision,
    equity              double precision,
    cash                double precision,
    debt_lt             double precision,
    debt_st             double precision,
    total_debt          double precision,
    current_assets      double precision,
    current_liabilities double precision,
    inventory           double precision,
    receivables         double precision,
    retained_earnings   double precision,
    ppe_net             double precision,

    -- flux de trésorerie
    ocf             double precision,
    capex           double precision,
    fcf             double precision,
    cff             double precision,
    dividends_paid  double precision,
    buybacks        double precision,
    issuance        double precision,

    -- traçabilité : de toute conclusion affichée on doit pouvoir remonter au dépôt
    accn          text,        -- accession principale ayant renseigné la ligne
    filed         date,        -- date de dépôt de cette accession
    inferred_zero text[]       not null default '{}',  -- champs mis à 0 par inférence
    q4_derived    boolean      not null default false, -- Q4 reconstruit (FY - 9M)
    source        text         not null default 'edgar',
    ingested_at   timestamptz  not null default now(),

    primary key (cik, period_end, fiscal_period)
);

create index fundamentals_period_idx on fundamentals (period_end desc);
create index fundamentals_fy_idx      on fundamentals (cik, fiscal_year)
    where fiscal_period = 'FY';

comment on column fundamentals.filed is
  'Règle de retraitement : à (cik, period_end, fiscal_period) identique, le dépôt le '
  'plus récent gagne. Mesuré chez MSFT : le CA 2016 vaut 85,3 Md$ sous ASC 605 et '
  '91,2 Md$ après retraitement ASC 606. Sans cette règle, une CAGR 5 ans est fausse.';

comment on column fundamentals.inferred_zero is
  'Champs dont l''absence de balise a été convertie en zéro sous garde-fou. Le front '
  'doit le signaler : une valeur inférée n''est pas une valeur déposée.';


-- Clause d'upsert canonique. Elle règle en une fois l'idempotence (relancer ne casse
-- rien) et le retraitement (un dépôt plus récent écrase). À utiliser telle quelle
-- côté ingestion :
--
--   insert into fundamentals (...) values (...)
--   on conflict (cik, period_end, fiscal_period) do update
--     set ... , ingested_at = now()
--     where excluded.filed is not null
--       and (fundamentals.filed is null or excluded.filed > fundamentals.filed);


-- =============================================================================
-- Journal d'ingestion — « je dois pouvoir savoir quels tickers ont échoué et pourquoi »
-- =============================================================================
create table ingestion_runs (
    run_id      bigint generated always as identity primary key,
    job         text        not null,          -- universe | prices | fundamentals
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    source_stamp text,                          -- ETag ou date du dump companyfacts.zip
    n_ok        integer not null default 0,
    n_failed    integer not null default 0,
    notes       text
);

create table ingestion_errors (
    run_id     bigint  not null references ingestion_runs (run_id) on delete cascade,
    ticker     text,
    cik        integer,
    stage      text not null,     -- fetch | parse | map | upsert
    reason     text not null,
    detail     text,
    occurred_at timestamptz not null default now()
);

create index ingestion_errors_run_idx on ingestion_errors (run_id);

-- Fraîcheur visible : une source cassée produit une donnée périmée et signalée,
-- jamais une page vide ou un chiffre faux.
create view data_freshness as
select c.ticker,
       c.mapping_profile,
       c.market_cap_as_of                             as cours_au,
       (select max(p.ym) from prices p where p.company_id = c.company_id) as dernier_mois_cote,
       (select max(f.period_end) from fundamentals f where f.cik = c.cik) as dernier_exercice,
       c.last_xbrl_filing,
       c.data_quality_score,
       c.updated_at
from companies c
where c.in_universe;


-- =============================================================================
-- RLS — usage mono-utilisateur : lecture publique, écriture réservée au service_role
-- =============================================================================
alter table companies        enable row level security;
alter table prices           enable row level security;
alter table fundamentals     enable row level security;
alter table ingestion_runs   enable row level security;
alter table ingestion_errors enable row level security;

create policy lecture_publique on companies    for select using (true);
create policy lecture_publique on prices       for select using (true);
create policy lecture_publique on fundamentals for select using (true);
-- Aucune policy sur les tables de journal : elles ne sont lisibles que par le
-- service_role, qui contourne RLS. Le front n'a pas à voir les logs d'ingestion.
