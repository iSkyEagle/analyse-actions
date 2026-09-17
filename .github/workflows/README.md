# Workflows d'ingestion

Quatre planifications, jamais simultanées : toutes partagent le groupe de concurrence
`ingestion`, parce que l'instance Supabase est en `nano` et sature sous charge
concurrente.

| Workflow | Cadence | Horaire UTC | Durée observée |
|---|---|---|---|
| `daily-prices` | lun-ven | 23:30 | ~5 min |
| `daily-backfill` | quotidien | 05:00 | 15-60 min, décroissant |
| `weekly-fundamentals` | dimanche | 03:00 | ~30 min |
| `monthly-universe` | 1er du mois | 04:00 | ~30 min |

## Secrets à créer

*Settings → Secrets and variables → Actions → New repository secret*

| Nom | Valeur |
|---|---|
| `DATABASE_URL` | la chaîne **Transaction pooler**, port `6543` |
| `SEC_USER_AGENT` | `Prénom Nom email@domaine` |

Le port `6543` n'est pas un détail : en mode session (`5432`), une connexion serveur
reste retenue pour toute la durée de la session, et un job interrompu ne la rend
jamais. Le pool se bouche et les exécutions suivantes échouent sur
`ECHECKOUTTIMEOUT`. En mode transaction, la connexion est rendue à chaque commit.

## Ce que chaque cadence porte

**Cours quotidiens.** Après la clôture US, dans les deux régimes horaires américains.
Ce job maintient aussi le projet Supabase éveillé : un projet gratuit se met en pause
après une semaine d'inactivité.

**Backfill.** Borné à 500 sociétés par passage. Un passage complet sur l'univers à
cinq ou dix ans d'historique écrase l'instance — constaté en conditions réelles.
L'univers atteint sa profondeur visée en une dizaine de jours, puis le job devient un
no-op : seules les sociétés à l'historique incomplet sont inscrites au plan.

**Fondamentaux.** Trois passages de 1 500 dans un seul job, pour ne télécharger le
dump de 1,41 Go qu'une fois — un runner repart d'un disque vide à chaque job. Pause de
deux minutes entre les passages. Le curseur `fundamentals_refreshed_at` fait reprendre
un lot interrompu là où il s'est arrêté.

**Univers.** Trois étapes dans un ordre qui compte. L'enrichissement SIC coûte un
appel par société, ~9 minutes : c'est la raison d'une cadence mensuelle. L'étape
intermédiaire ingère les cours de **tous les candidats**, pas seulement de l'univers
retenu — sans elle, une société sortie de l'univers ne recevrait plus de cours, donc
plus de volume médian, et n'aurait aucun moyen d'y revenir si sa capitalisation
remontait. L'univers se figerait sans que rien ne le signale.

## Après un changement de logique d'extraction

Lancer `weekly-fundamentals` manuellement avec `force` coché. À date de dépôt
inchangée, la clause d'upsert ignore autrement la réécriture — et le fait en silence.

## Points de surveillance

GitHub désactive les workflows planifiés d'un dépôt resté 60 jours sans activité. Un
commit suffit à réarmer.

En cas d'échec, vérifier d'abord le journal en base plutôt que les logs du runner :

```sql
select job, started_at, n_ok, n_failed, notes
from ingestion_runs order by run_id desc limit 10;

select ticker, stage, reason, left(detail, 80)
from ingestion_errors where run_id = (select max(run_id) from ingestion_runs)
limit 20;
```

Et la fraîcheur des données, qui dit ce qui date sans dire pourquoi :

```sql
select count(*) filter (where prices_refreshed_at < current_date - 3)       as cours_perimes,
       count(*) filter (where fundamentals_refreshed_at < current_date - 14) as comptes_perimes
from companies where in_universe;
```
