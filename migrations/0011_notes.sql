-- =============================================================================
-- 0011_notes.sql — Notes par axe et note globale (étape 3c)
--
-- La table `scores` existe depuis 0002, avec la règle 2 du mémoire imposée en base : un
-- axe couvert à moins de 50 % n'a pas de note. Deux ajustements :
--   - un seul instantané par société et par axe : la clé gardait un historique par date ;
--   - la note globale devient un axe à part entière (règle 3 révisée).
-- La note est stockée sur 100 avec sa précision, affichée sur 10 sans décimale.
-- =============================================================================

alter type score_axis_t add value if not exists 'globale';

delete from scores s using scores t
where s.company_id = t.company_id and s.axis = t.axis and s.as_of < t.as_of;

alter table scores drop constraint if exists scores_pkey;
alter table scores add constraint scores_pkey primary key (company_id, axis);

comment on table scores is
  'Notes par axe (valorisation, qualité, croissance, dilution et risque) et note globale. '
  'Score sur 100, affiché sur 10. Pas de note sous 50 % de couverture ; pas de note '
  'globale si moins de trois axes sont notés (règle 3).';
