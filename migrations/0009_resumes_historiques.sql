-- =============================================================================
-- 0009_resumes_historiques.sql — Résumés sur 5 ans des multiples (étape 4c)
--
-- La série quotidienne des multiples point-in-time n'est jamais stockée : elle se
-- recalcule à partir des cours et des comptes. Seul son résumé l'est — de quoi
-- dessiner la règle historique à côté de chaque multiple. Une ligne par société et
-- par multiple, soit environ 2 Mo pour l'univers.
-- =============================================================================

create table if not exists metric_history (
    company_id smallint not null references companies (company_id) on delete cascade,
    metric_id  text     not null,
    as_of      date     not null,
    n          integer  not null,          -- séances où le multiple était défini
    couverture real     not null,          -- n / séances de la fenêtre
    p10        double precision,
    mediane    double precision,
    p90        double precision,
    mini       double precision,
    maxi       double precision,
    primary key (company_id, metric_id)
);

comment on table metric_history is
  'Résumé sur 5 ans de chaque multiple point-in-time : chaque séance n''utilise que '
  'les comptes publiés à sa date (first_filed), et des nombres d''actions ramenés sur '
  'la base des cours. Sous 50 % de couverture, la règle historique n''est pas affichée.';

alter table metric_history enable row level security;
create policy lecture_publique on metric_history for select using (true);
