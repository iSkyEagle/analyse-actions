-- =============================================================================
-- 0003_ingestion_state.sql — Fraîcheur par source, et reprise des lots
--
-- Deux besoins que `updated_at` seul ne couvre pas :
--
--   * Reprise. Le rafraîchissement hebdomadaire des fondamentaux traite ~4 500
--     sociétés. Un job GitHub Actions est plafonné à 6 heures et peut être interrompu.
--     En triant par date de dernier rafraîchissement croissante, un lot interrompu
--     reprend là où il s'est arrêté sans état externe à conserver.
--
--   * Fraîcheur visible. `updated_at` est touché par n'importe quel job : il ne dit
--     pas si ce sont les cours ou les comptes qui datent. Le garde-fou du mémoire
--     — « toute donnée affichée porte sa date » — demande de distinguer les deux.
-- =============================================================================

alter table companies
    add column if not exists fundamentals_refreshed_at date,
    add column if not exists prices_refreshed_at       date;

-- Index partiel : le job trie les seules sociétés de l'univers, les autres ne sont
-- jamais lues. `nulls first` place naturellement en tête celles jamais traitées.
create index if not exists companies_fund_staleness_idx
    on companies (fundamentals_refreshed_at nulls first)
    where in_universe;

create index if not exists companies_price_staleness_idx
    on companies (prices_refreshed_at nulls first)
    where in_universe;

comment on column companies.fundamentals_refreshed_at is
  'Date du dernier passage réussi du job fondamentaux sur cette société. Sert de '
  'curseur de reprise et de témoin de fraîcheur, indépendamment des cours.';

-- La vue de fraîcheur distingue désormais les deux sources.
drop view if exists data_freshness;
create view data_freshness as
select c.ticker,
       c.mapping_profile,
       c.in_universe,
       c.prices_refreshed_at,
       (select max(p.ym) from prices p where p.company_id = c.company_id) as dernier_mois_cote,
       c.fundamentals_refreshed_at,
       (select max(f.period_end) from fundamentals f where f.cik = c.cik) as dernier_exercice,
       c.last_xbrl_filing,
       c.data_quality_score,
       c.market_cap_as_of,
       c.updated_at
from companies c;
