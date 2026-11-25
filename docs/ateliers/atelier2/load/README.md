# Atelier 2, étape 3 : test de charge Locust

Scénario : beaucoup de parieurs arrivent en même temps sur le même pari, juste avant la clôture. Fichiers :

- `tests/load/seed.py` crée un bookie, un pari ouvert et N parieurs, chacun inscrit, connecté, crédité de 20 € par le PSP simulé (vrai dépôt, vrai clic « Payer », vrai webhook signé) et entré dans le pari par un lien d'invitation. Tout passe par l'API, aucune route spéciale dans l'application ;
- `tests/load/locustfile.py` contient deux profils. `Bettor` se connecte, lit le pari (poids 5), son portefeuille (2), la liste de ses paris (1) et mise une fois (3), avec 1 à 3 s entre deux actions. `StakeRush` reprend les jetons du seed et mise dès son arrivée, puis surveille son portefeuille ;
- `tests/load/capture_ui.py` prend les captures de l'interface web de Locust avec Playwright headless ;
- `tests/load/test_live_overspend.py` : la question du solde négatif, voir `solde-negatif.md`.

Les invitations sont acceptées pendant le seed et pas pendant le test : un pari ne peut pas avoir plus de 50 invitations actives (US-06.4), on ne peut donc pas préparer 1 500 liens d'avance pour un seul pari.

## Pile testée

PostgreSQL 17 (même image que `docker-compose.yml`) dans Docker avec le rôle applicatif restreint et `grants.sql`, l'application en uvicorn avec 4 workers, le PSP simulé, Locust et le navigateur des captures. Tout tourne sur un MacBook (11 cœurs), PostgreSQL dans la VM Colima (4 vCPU, 6 Go). Locust n'a jamais signalé de CPU saturé. Les chiffres sont à lire en relatif : la machine sert à la fois de client et de serveur.

## Lancer

```sh
uv sync --group load --group e2e
docker run -d --rm --name toutbet-load-pg \
  -e POSTGRES_DB=toutbet -e POSTGRES_USER=toutbet_owner -e POSTGRES_PASSWORD=owner-load-only \
  -e APP_DB_PASSWORD=app-load-only \
  -v "$PWD/deploy/postgres/create-app-role.sh:/docker-entrypoint-initdb.d/10-create-app-role.sh:ro" \
  -p 127.0.0.1:55433:5432 postgres:17
export DATABASE_URL=postgresql+psycopg://toutbet_app:app-load-only@127.0.0.1:55433/toutbet
DATABASE_ADMIN_URL=postgresql+psycopg://toutbet_owner:owner-load-only@127.0.0.1:55433/toutbet \
  uv run toutbet-admin init-db --grants deploy/postgres/grants.sql

set -a; eval "$(uv run toutbet-admin gen-secrets)"; set +a
export ENV=test PSP_BASE_URL=http://127.0.0.1:8201 PUBLIC_BASE_URL=http://127.0.0.1:8200 \
  ALLOWED_HOSTS=127.0.0.1 TRUSTED_PROXY_IPS=192.0.2.1 \
  MOCK_PSP_PORT=8201 MOCK_PSP_PUBLIC_URL=http://127.0.0.1:8201 \
  MOCK_PSP_WEBHOOK_URL=http://127.0.0.1:8200/api/webhooks/psp
uv run mock-psp &

# limites relevées pour le seed (et pour le run 2)
for v in LOGIN REGISTER REFRESH WRITE READ WRITE_PER_IP READ_PER_IP KYC WITHDRAWALS INVITATION_ACCEPT WEBHOOK; do
  export RATE_LIMIT_$v=1000000/minute
done
uv run uvicorn --factory toutbet.main:build_app --port 8200 --workers 4 --no-server-header --no-access-log --no-proxy-headers &
uv run --group load python tests/load/seed.py --bettors 1500 --workers 16 --out /tmp/seed.json

# run 1 : relancer uvicorn sans les RATE_LIMIT_* (limites de production), run 2 : garder les limites relevées
TOUTBET_LOAD_SEED=/tmp/seed.json uv run --group load locust -f tests/load/locustfile.py Bettor \
  --host http://127.0.0.1:8200 --web-host 127.0.0.1 --web-port 8089 \
  --autostart -u 1500 -r 10 -t 4m --autoquit 20 --csv run2 --csv-full-history
uv run --group e2e python tests/load/capture_ui.py --prefix docs/ateliers/atelier2/load/run2

# rafale de mises : seed neuf (les jetons du seed valent 15 min), puis
TOUTBET_LOAD_SEED=/tmp/seed.json uv run --group load locust -f tests/load/locustfile.py StakeRush \
  --host http://127.0.0.1:8200 --autostart -u 3000 -r 200 -t 75s --autoquit 15 --csv rush

uv run toutbet-admin verify-ledger
docker stop toutbet-load-pg
```

Chaque run part d'un seed neuf pour que chaque parieur ait encore sa mise à faire. Pendant les runs, on relevait toutes les 5 s `pg_stat_activity` (attentes par type), le CPU de PostgreSQL et celui des workers.

## Résultats

| Run | Limites | Utilisateurs | Requêtes | RPS | p95 | Échecs | 5xx |
|---|---|---|---|---|---|---|---|
| 1 `Bettor` | production | 500 (25/s), 3 min | 43 249 | 240 | 11 ms | 91,6 % (tous des 429 sur la connexion) | 0 |
| 2 `Bettor` | relevées | 1 500 (10/s), 4 min | 82 291 | 343 en moyenne, 478 au pic | 2,5 s | 0 | 0 |
| rafale `StakeRush` | relevées | 3 000 (200 mises/s), 75 s | 36 948 | 492 | mises 27 s | 22 % (portefeuille seulement) | 2 801 |
| rafale 50/s `StakeRush` | relevées | 600 (50 mises/s), 45 s | 12 257 | 278 | mises 830 ms | 0 | 0 |

`toutbet-admin verify-ledger` passe après chaque run : aucune mise perdue ou payée deux fois, même pendant la rafale.

Les chiffres détaillés sont dans les CSV de Locust : `run1_stats.csv`, `run2_stats.csv`, `rush_stats.csv`, `rush50_stats.csv`, les échecs dans `*_failures.csv`, et une version réduite de l'historique (toutes les 10 s, lignes agrégées) dans `run1_history.csv`, `run2_history.csv`, `rush_history.csv`.

Captures de l'interface web de Locust pendant les tests :

| Run | Statistiques | Graphiques | Échecs |
|---|---|---|---|
| 1 | ![](run1-statistics.png) | ![](run1-charts.png) | ![](run1-failures.png) |
| 2 | ![](run2-statistics.png) | ![](run2-charts.png) | ![](run2-failures.png) |
| rafale | ![](rush-statistics.png) | ![](rush-charts.png) | ![](rush-failures.png) |

## Analyse

**Run 1, limites de production : les mécanismes de sécurité saturent avant l'application.** Tous les utilisateurs Locust viennent de la même IP, comme un stade derrière un NAT d'opérateur. La connexion est limitée à 5 par minute et par IP : sur 39 670 tentatives, 60 seulement passent en 3 minutes, les autres reçoivent 429 en 2 ms, avant tout calcul argon2. Les 60 connectés lisent et misent sans souci (p95 18 ms en lecture, 63 ms en mise). Le serveur reste à 30 % d'un cœur. La protection contre la force brute (AS-01) fait son travail, mais bloque aussi les vrais parieurs d'un même réseau.

**Run 2, limites relevées : ça casse vers 500 à 700 utilisateurs.** Le p95 passe 300 ms vers 500 utilisateurs (220 RPS), 1 s vers 700, et 4,5 s à 1 300 utilisateurs. Le débit plafonne à 478 RPS, après quoi les requêtes font la queue : aucune erreur, aucune 5xx, mais une attente de plusieurs secondes. Même `GET /api/wallet`, la requête la plus légère, monte à 2,4 s au p95 : le goulot n'est pas une route en particulier. Les 4 workers Python sont à 300-380 % de CPU (presque 4 cœurs pleins, un par worker à cause du GIL) et PostgreSQL à 200-260 %. Dans `pg_stat_user_tables`, la table `users` a subi 68 087 parcours complets pour 206 millions de lignes lues : chaque lecture du pari reconstruit la liste complète des 1 500 pseudos participants (jointure `users`/`participants` et tri), soit 26 Ko de JSON par réponse.

**Rafale de mises : le verrou de tête du grand livre est le vrai goulot.** Chaque mouvement d'argent verrouille la ligne `chain_heads` (`SELECT ... FOR UPDATE`) jusqu'au commit, pour chaîner l'écriture (design.md §7.4). Avec 200 mises/s demandées, le débit réel plafonne à 100-110 mises/s, PostgreSQL montre 50 à 56 sessions en attente `Lock: tuple` sur cette ligne, et le p95 d'une mise monte à 27 s (médiane 7,3 s). Toutes les connexions du pool attendent le même verrou, donc les autres requêtes attendent aussi. À 50 mises/s le p95 est déjà de 830 ms. Le SLO de `STEERING.md` (« p95 d'une mise < 300 ms à 200 mises/s sur un même pari ») n'est pas tenu. Après la rafale, les 3 000 utilisateurs interrogent leur portefeuille à 650-1 000 RPS, au-dessus de la capacité de lecture : 2 801 réponses 500 (`OperationalError: Can't assign requested address` vers PostgreSQL) et 5 346 timeouts côté Locust. Le corps des 500 reste générique, la trace n'est que dans le log serveur.

**Ce qui n'est pas le goulot ici :**

- argon2 : en run 2 la connexion n'arrive qu'à 6 par seconde (p95 1,6 s pendant la montée, à cause de la file d'attente générale). En run 1 la limite répond avant le hash. Un afflux de connexions le deviendrait : environ 50 ms de CPU par hash, donc une vingtaine de connexions par seconde et par cœur ;
- les appels synchrones au PSP : ils ne sont pas sur le chemin de ce scénario. Les plus de 6 000 dépôts des seeds sont passés, avec un seul reset de connexion à 24 threads, relancé à 16.

## Known issues

- **Verrou unique du grand livre.** Toutes les mises du site passent une par une, environ 100 par seconde sur cette machine. Pistes : une chaîne par pari ou par compte (prévu dans design.md §7.4), prendre le verrou le plus tard possible, ou un seul écrivain qui chaîne par lots.
- **Limiteur en mémoire par processus.** slowapi stocke les compteurs en `memory://`, donc chaque worker a les siens. Avec 4 workers, on a compté 13 mauvaises connexions acceptées depuis la même IP avant le premier 429, au lieu de 5. En production il faut un stockage partagé (Redis).
- **Limite de connexion par IP trop stricte pour un NAT.** 60 connexions en 3 minutes pour 500 parieurs d'un même réseau. Pistes : compter par couple IP + email avec un plafond global plus haut, ou un délai progressif.
- **Liste des participants non bornée.** Chaque lecture d'un pari renvoie tous les pseudos (26 Ko pour 1 500 participants). Il faudrait paginer ou renvoyer un nombre.
- **Pool de connexions par défaut.** SQLAlchemy garde 5 connexions par worker et en ouvre 10 de plus en pointe, refermées aussitôt. Sous surcharge, ces ouvertures et fermetures épuisent les ports locaux et finissent en 500. Il faut régler `pool_size` (ou passer par PgBouncer) et répondre 503 avec `Retry-After` quand la base est injoignable.
