-- =============================================================================
-- 0007_metriques_cle.sql — Une clé par société et par métrique, sans la date
--
-- `metrics` ne conserve qu'un instantané par société (0006). Sa clé primaire
-- (company_id, as_of, metric_id), héritée de 0002, incluait pourtant la date de calcul.
-- Conséquence : chaque jour, toutes les métriques avaient une clé nouvelle, aucune
-- insertion ne rencontrait de conflit, et le job réinsérait tout puis supprimait tout
-- l'ancien — ~91 000 versions de lignes à écrire puis à nettoyer chaque soir, sur une
-- instance nano déjà saturée en attente disque.
--
-- Avec la clé (company_id, metric_id), une métrique inchangée n'est plus réécrite :
-- seuls les multiples, qui suivent le cours, changent d'un jour à l'autre.
--
-- `as_of` devient la date du dernier CHANGEMENT de valeur, plus celle du dernier
-- calcul. La fraîcheur du calcul se lit dans companies.prices_refreshed_at.
-- =============================================================================

-- Par sécurité, un seul instantané par couple avant de poser la nouvelle clé.
delete from metrics m
using metrics n
where m.company_id = n.company_id
  and m.metric_id = n.metric_id
  and m.as_of < n.as_of;

alter table metrics drop constraint if exists metrics_pkey;
alter table metrics add constraint metrics_pkey primary key (company_id, metric_id);

comment on column metrics.as_of is
  'Date du dernier changement de la valeur ou de sa raison. Une métrique inchangée '
  'n''est pas réécrite, sa date reste celle où elle a pris sa valeur actuelle.';
