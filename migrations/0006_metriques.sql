-- =============================================================================
-- 0006_metriques.sql — Stockage des métriques calculées à l'ingestion
--
-- Les métriques sont calculées une seule fois, en Python (ingestion/metriques.py), et
-- lues par l'interface. Deux ajustements du schéma de 0002 :
--
-- 1. Une RAISON par métrique absente. Règle 2 du mémoire : un indicateur non
--    applicable s'affiche avec sa raison, jamais comme un zéro ni comme un vide.
--
-- 2. Un seul instantané par société, pas d'historique mensuel. 0002 prévoyait un
--    instantané par mois sur 24 mois : à 31 métriques pour ~4 800 sociétés, cela
--    fait 3,6 millions de lignes, environ 200 Mo — la moitié du tier gratuit.
--    L'historique des multiples se recalcule à partir des cours et des comptes ; il
--    n'a pas à être stocké.
--
-- Les séries annuelles (marges, rentabilité sur les derniers exercices) ont leur
-- propre table : ce ne sont pas des instantanés datés du jour, mais des valeurs
-- attachées à un exercice, et elles doivent survivre aux recalculs quotidiens.
-- =============================================================================

alter table metrics add column if not exists raison text;

comment on column metrics.raison is
  'Pourquoi la valeur est absente, déduit des données — jamais supposé. La ligne de '
  'metric_id ''_base_12_mois'' porte dans ce champ la base de calcul des flux sur '
  '12 mois (quatre trimestres, ou repli sur le dernier exercice) : traçabilité.';

-- La purge mensuelle de 0002 est retirée. Avec un seul instantané par société, elle
-- deviendrait dangereuse : elle supprime tout ce qui est antérieur à la date la plus
-- récente de la table ENTIÈRE, donc effacerait une société dont le recalcul du jour
-- aurait échoué — alors que sa dernière valeur connue reste la meilleure disponible.
drop function if exists purge_metrics_history();

create table if not exists metric_series (
    company_id smallint not null references companies (company_id) on delete cascade,
    metric_id  text     not null,
    period_end date     not null,
    value      double precision not null,
    primary key (company_id, metric_id, period_end)
);

comment on table metric_series is
  'Ratios par exercice (marges, rentabilité, endettement) : la tendance sur les '
  'derniers exercices que le mémoire demande pour l''axe Qualité.';

alter table metric_series enable row level security;
create policy lecture_publique on metric_series for select using (true);
