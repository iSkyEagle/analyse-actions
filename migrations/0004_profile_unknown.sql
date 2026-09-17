-- =============================================================================
-- 0004_profile_unknown.sql — Valeur manquante dans mapping_profile_t
--
-- `out_of_scope()` distingue cinq cas hors périmètre : ifrs, foreign, fund, spac,
-- et « aucun concept us-gaap » — renvoyé sous le libellé 'unknown'. Ce dernier
-- manquait à l'énumération : l'UPDATE échouait, empoisonnait la transaction, et
-- interrompait le lot entier à la première société concernée.
--
-- Cas réel rencontré : société déposant un 10-Q récent mais sans aucune balise
-- us-gaap exploitable. Ni IFRS, ni fonds, ni SPAC — simplement inexploitable.
-- =============================================================================

alter type mapping_profile_t add value if not exists 'unknown';
