-- =============================================================================
-- 0012_actions_estimees.sql — Nombre d'actions de repli (13 octobre 2026)
--
-- Une société à plusieurs catégories d'actions (NuScale, indie…) déclare son nombre
-- d'actions par catégorie ; companyfacts, qui supprime les dimensions, n'en garde rien.
-- Sans nombre d'actions, pas de capitalisation, donc hors univers : 412 sociétés.
-- Repli sur les actions diluées de la dernière période déposée ; cette colonne le
-- signale, et rend la capitalisation incertaine.
-- =============================================================================

alter table companies add column if not exists shares_estimated boolean not null default false;

comment on column companies.shares_estimated is
  'Vrai si shares_outstanding vient du repli sur les actions diluées de la dernière période '
  '(dei:EntityCommonStockSharesOutstanding absent, plusieurs catégories d''actions). '
  'La capitalisation est alors marquée incertaine.';
