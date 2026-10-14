-- =============================================================================
-- 0013_comparables.sql — Sociétés comparables (étape 3d)
--
-- Les cinq sociétés les plus proches de chaque société de l'univers : même industrie
-- et même profil (le secteur sous six sociétés), puis la taille et, à poids égal à
-- elle seule, marge brute, marge opérationnelle et croissance du chiffre d'affaires.
-- Calculées chaque soir par ingestion/comparables.py ; un seul instantané.
-- =============================================================================

create table if not exists comparables (
    company_id    smallint not null references companies(company_id) on delete cascade,
    rang          smallint not null check (rang between 1 and 5),
    comparable_id smallint not null references companies(company_id) on delete cascade,
    distance      real not null,
    niveau        text not null check (niveau in ('industrie', 'secteur')),
    as_of         date not null,
    primary key (company_id, rang)
);

comment on table comparables is
  'Cinq sociétés les plus proches de chaque société de l''univers (étape 3d). Distance '
  'de 0 à 1 : taille (poids 3), marge brute, marge opérationnelle, croissance du chiffre '
  'd''affaires sur 3 ans (poids 1 chacune), par rang dans le groupe de candidates.';

alter table comparables enable row level security;
drop policy if exists lecture_publique on comparables;
create policy lecture_publique on comparables for select using (true);
