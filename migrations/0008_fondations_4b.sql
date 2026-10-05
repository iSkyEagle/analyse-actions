-- =============================================================================
-- 0008_fondations_4b.sql — Fondations de données de l'étape 4b
--
-- 1. Comptes : première publication, source du nombre d'actions, flux reconstitués.
-- 2. Cours : base « ajustée des seules divisions », divisions et dividendes par action.
-- 3. Indices de référence : ^GSPC et ^SP500TR, stockés comme lignes de `companies`.
--
-- Ajouts de colonnes à valeur par défaut constante : simple mise à jour du catalogue
-- depuis Postgres 11, aucune réécriture de table.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. Comptes
-- ---------------------------------------------------------------------------
alter table fundamentals add column if not exists first_filed date;
alter table fundamentals add column if not exists shares_filed date;
alter table fundamentals add column if not exists ytd_derived text[] not null default '{}';

comment on column fundamentals.first_filed is
  'Première publication de la période : le dépôt le plus ancien qui la cite. `filed` '
  'est au contraire le dernier — un 10-K présente trois exercices —, jusqu''à deux ans '
  'plus tard. Toute série rapprochant cours et comptes s''aligne sur first_filed.';
comment on column fundamentals.shares_filed is
  'Dépôt d''où vient shares_diluted. Un dépôt postérieur à une division d''actions '
  'retraite les exercices qu''il présente, un dépôt antérieur non : cette date permet '
  'de ramener chaque nombre d''actions sur la base actuelle.';
comment on column fundamentals.ytd_derived is
  'Champs obtenus par différence de cumuls (T2 = 6 mois - T1, T3 = 9 mois - 6 mois). '
  'Dans un 10-Q, le tableau des flux de trésorerie n''est publié qu''en cumul.';

-- ---------------------------------------------------------------------------
-- 2. Cours
-- ---------------------------------------------------------------------------
alter table companies add column if not exists prices_rebuilt_at date;

comment on column companies.prices_rebuilt_at is
  'Dernière reconstruction complète des cours sur la base « ajustée des seules '
  'divisions ». NULL : historique encore ajusté des dividendes, ou à reconstruire '
  'parce qu''une nouvelle division a été détectée.';

create table if not exists splits (
    company_id smallint not null references companies (company_id) on delete cascade,
    date       date     not null,
    ratio      real     not null check (ratio > 0),
    primary key (company_id, date)
);
comment on table splits is
  'Divisions d''actions, lues dans yfinance avec les cours. ratio = 10 pour une '
  'division par dix, 0,1 pour un regroupement de dix actions en une.';

create table if not exists dividends (
    company_id smallint not null references companies (company_id) on delete cascade,
    date       date     not null,
    amount     real     not null check (amount > 0),
    primary key (company_id, date)
);
comment on table dividends is
  'Dividendes par action, à la date de détachement, en unités ajustées des divisions. '
  'Servent à reconstituer le rendement total ; les montants versés au sens comptable '
  'restent lus dans EDGAR (fundamentals.dividends_paid).';

alter table splits enable row level security;
alter table dividends enable row level security;
create policy lecture_publique on splits for select using (true);
create policy lecture_publique on dividends for select using (true);

-- ---------------------------------------------------------------------------
-- 3. Indices de référence
-- ---------------------------------------------------------------------------
-- Un indice n'a pas de CIK. Les lignes elles-mêmes sont créées par le job des cours :
-- une valeur d'énumération ajoutée ne peut pas être utilisée dans la transaction qui
-- l'ajoute.
alter type mapping_profile_t add value if not exists 'index';

alter table companies alter column cik drop not null;
alter table companies drop constraint if exists companies_cik_sauf_indice;
alter table companies add constraint companies_cik_sauf_indice
  check (cik is not null or mapping_profile::text = 'index');
