-- =============================================================================
-- 0010_percentiles.sql — Percentiles sectoriels (étape 3b)
--
-- Chaque métrique d'une société est située dans son groupe de comparaison : son
-- industrie si elle compte au moins 15 sociétés de l'univers pour la métrique, sinon
-- son secteur. La colonne metrics.percentile (0-100) existe depuis 0002 ; on y ajoute
-- le niveau du groupe et son effectif, pour que l'interface puisse écrire « au-dessus de
-- 87 % des 112 sociétés comparables ».
--
-- sector_stats (0002) est remplacée par groupe_stats : jamais remplie, sa clé gardait
-- un historique par date — contraire à la règle d'un seul instantané — et elle ne
-- connaissait que les secteurs.
-- =============================================================================

alter table metrics add column if not exists percentile_niveau text;
alter table metrics add column if not exists percentile_n smallint;

alter table metrics drop constraint if exists metrics_niveau_valide;
alter table metrics add constraint metrics_niveau_valide
  check (percentile_niveau is null or percentile_niveau in ('industrie', 'secteur'));

comment on column metrics.percentile is
  'Part du groupe de comparaison située sous la société, en pour cent, les égalités '
  'comptant pour moitié. Null : métrique non applicable, ou groupe de moins de 15 sociétés.';
comment on column metrics.percentile_niveau is
  'Groupe de comparaison : industrie si elle compte au moins 15 sociétés de l''univers '
  'pour la métrique, sinon secteur.';

drop table if exists sector_stats;

create table if not exists groupe_stats (
    niveau    text not null check (niveau in ('industrie', 'secteur')),
    groupe    text not null,
    metric_id text not null,
    as_of     date not null,
    n         integer not null check (n >= 15),
    p10 double precision, p25 double precision, mediane double precision,
    p75 double precision, p90 double precision,
    primary key (niveau, groupe, metric_id)
);

comment on table groupe_stats is
  'Distribution de chaque métrique par industrie et par secteur, sur les seules sociétés '
  'de l''univers. Sous 15 sociétés, pas de statistique plutôt qu''une statistique fausse.';

alter table groupe_stats enable row level security;
create policy lecture_publique on groupe_stats for select using (true);
