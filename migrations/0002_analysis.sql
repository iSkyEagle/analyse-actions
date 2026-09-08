-- =============================================================================
-- 0002_analysis.sql — Tables d'analyse (peuplées en phases 3 et 4)
--
-- Décision de forme : `metrics` est en format LONG (une ligne par métrique), pas en
-- colonnes. Raison : le §5 du mémoire impose que la configuration des métriques vive
-- dans le code et reste lisible d'un coup d'œil. Un schéma en colonnes obligerait à
-- une migration à chaque métrique ajoutée ou renommée — la base deviendrait la copie
-- désynchronisée du fichier de configuration. Le format long aligne aussi `metrics`
-- sur `sector_stats`, déjà long dans le modèle du mémoire.
--
-- Coût mesuré : ~60 métriques × 4 500 sociétés = 270 000 lignes, ~15 Mo. Le prix à
-- payer est un pivot dans le screener, sur 4 500 lignes : négligeable.
-- =============================================================================

create type score_axis_t as enum ('valorisation', 'qualite', 'croissance', 'dilution');
create type severity_t   as enum ('info', 'attention', 'alerte');


-- =============================================================================
-- metrics — valeur d'une métrique et son rang sectoriel
-- =============================================================================
create table metrics (
    company_id smallint not null references companies (company_id) on delete cascade,
    as_of      date     not null,
    metric_id  text     not null,          -- identifiant déclaré dans le code
    value      double precision,
    percentile smallint,                   -- rang dans le secteur, 0-100, null si absolu
    is_applicable boolean not null default true,

    primary key (company_id, as_of, metric_id),
    constraint metrics_pct_range check (percentile between 0 and 100)
);

create index metrics_screener_idx on metrics (metric_id, as_of, value);

comment on column metrics.is_applicable is
  'Règle 2 du mémoire : un indicateur non applicable est exclu, jamais noté 0. La ligne '
  'existe pour que le taux de couverture de l''axe soit calculable et affichable.';

-- Rétention : les métriques sont recalculées à chaque ingestion. Conserver toutes les
-- valeurs quotidiennes coûterait ~100 Mo par an pour aucun usage produit. On ne garde
-- que la dernière date, plus un instantané par mois sur 24 mois.
create or replace function purge_metrics_history() returns void language sql as $$
    delete from metrics m
    where m.as_of < (select max(as_of) from metrics)
      and m.as_of <> (select min(x.as_of) from metrics x
                      where date_trunc('month', x.as_of) = date_trunc('month', m.as_of));
$$;


-- =============================================================================
-- sector_stats — distribution sectorielle, base de la notation contextuelle
-- =============================================================================
create table sector_stats (
    sector    text not null,
    metric_id text not null,
    as_of     date not null,
    n         integer not null,            -- effectif ayant servi au calcul
    p10 double precision, p25 double precision, median double precision,
    p75 double precision, p90 double precision,

    primary key (sector, metric_id, as_of),
    constraint sector_stats_min_n check (n >= 5)
);

comment on constraint sector_stats_min_n on sector_stats is
  'Un percentile calculé sur moins de 5 sociétés n''est pas un percentile. Les secteurs '
  'trop peu peuplés ne produisent pas de statistique plutôt qu''une statistique fausse.';


-- =============================================================================
-- scores — quatre sous-scores, jamais un score unique (règle 3 du mémoire)
-- =============================================================================
create table scores (
    company_id   smallint     not null references companies (company_id) on delete cascade,
    as_of        date         not null,
    axis         score_axis_t not null,
    score        smallint,                    -- null si couverture insuffisante
    coverage_pct smallint     not null,
    n_applicable smallint     not null,

    primary key (company_id, as_of, axis),
    constraint scores_range check (score between 0 and 100),
    -- Un axe couvert à moins de 50 % n'affiche aucun score. La contrainte l'impose
    -- en base : le front ne peut pas contourner la règle.
    constraint scores_coverage_gate check (
        (coverage_pct >= 50 and score is not null)
        or (coverage_pct < 50 and score is null)
    )
);


-- =============================================================================
-- insights — phrases produites par le moteur de règles (phase 4)
-- =============================================================================
create table insights (
    company_id    smallint    not null references companies (company_id) on delete cascade,
    as_of         date        not null,
    rule_id       text        not null,
    severity      severity_t  not null,
    salience      real        not null,     -- ampleur de l'écart à la médiane sectorielle
    rendered_text text        not null,
    evidence      jsonb       not null default '{}',   -- {metric_id: valeur} ayant déclenché

    primary key (company_id, as_of, rule_id)
);

create index insights_salience_idx on insights (company_id, as_of, salience desc);

comment on column insights.evidence is
  'Traçabilité : chaque phrase affichée doit être cliquable vers le chiffre qui l''a '
  'déclenchée, puis vers le dépôt EDGAR source via fundamentals.accn.';


alter table metrics      enable row level security;
alter table sector_stats enable row level security;
alter table scores       enable row level security;
alter table insights     enable row level security;

create policy lecture_publique on metrics      for select using (true);
create policy lecture_publique on sector_stats for select using (true);
create policy lecture_publique on scores       for select using (true);
create policy lecture_publique on insights     for select using (true);
