# Atelier 4, étape 2 : supervision et logs

Version du 2026-11-25. Les règles ci-dessous s'appuient sur les événements que l'application écrit vraiment. L'application écrit une ligne JSON par événement sur la sortie standard (`src/toutbet/observability.py`, `log_event`), avec `ts` en UTC, `level`, `event`, `request_id`, puis les champs de l'événement.

## 1. Événements disponibles

Relevés avec `grep -rn "log_event(" src/`. Ceux en gras ont été ajoutés pendant cet atelier parce qu'une règle en avait besoin.

| Événement | Champs utiles | Émis par |
|---|---|---|
| `http.request` | `method`, `route` (gabarit), `status`, `duration_ms`, `client_ip`, `user_id` | `middleware/request_id.py`, une ligne par requête |
| `error.unhandled` | trace masquée | `middleware/request_id.py`, toute réponse 500 |
| `auth.login_failed` | `reason` (`unknown_account`, `locked`, `bad_password`), `user_id` | `services/accounts.py` |
| `auth.account_locked` | `user_id` | `services/accounts.py`, 10 échecs en 15 min |
| `auth.login_succeeded` | `user_id` | `services/accounts.py` |
| `auth.token_rejected` | `reason` : `missing`, `invalid`, **`bad_signature`**, `unknown_session`, `inactive_session` | `security/authz.py` |
| `auth.reauthentication_failed` | `user_id` | `api/withdrawals.py`, mauvais mot de passe au retrait |
| `authz.denied` | `user_id`, `reason` | `security/authz.py`, `services/bets.py`, `services/deposits.py`, `services/invitations.py` |
| `rate_limit.exceeded` | `client_ip`, `user_id`, `route` | `rate_limit.py`, chaque 429 |
| `psp.webhook_rejected` | `client_ip` | `api/webhooks.py`, signature ou horodatage invalide |
| `psp.amount_mismatch` | `deposit_id`, `user_id`, `event_id` | `services/deposits.py` |
| `psp.timeout` | `user_id`, `cause` | `api/deposits.py`, `api/kyc.py`, PSP en erreur ou hors délai |
| **`money.deposit_failed`** | `user_id`, `deposit_id`, `reason` | `services/deposits.py`, webhook `payment.failed` |
| `money.deposit_credited` | `user_id`, `deposit_id` | `services/deposits.py` |
| `money.withdrawal_unconfirmed` | `withdrawal_id`, `cause`, `user_id` | `api/withdrawals.py`, `services/withdrawals.py` (worker) |
| `money.withdrawal_failed` | `user_id`, `withdrawal_id`, `reason` | `services/withdrawals.py`, virement refusé et recrédité |
| `money.withdrawal_reconciled` | `withdrawal_id`, `status` | `services/withdrawals.py` |
| `money.withdrawal_rejected`, `money.stake_rejected` | `user_id`, `reason` | `services/withdrawals.py`, `services/stakes.py` |
| `moderation.decision` | `moderator_id`, `bet_id`, `outcome` | `services/moderation.py` |
| `admin.moderator_created`, **`admin.access_revoked`** | `user_id` | `cli.py` |
| `ledger.verify_failed` | `detail` | `cli.py`, `toutbet-admin verify-ledger` |
| `ledger.head`, `ledger.head_missing` | numéros et hash de tête | `worker.py`, toutes les 5 min |
| `worker.tick`, `worker.tick_failed` | compteurs du tour | `worker.py`, toutes les 30 s |

Les trois ajouts, chacun avec son test :

- `money.deposit_failed` : avant, un paiement refusé passait le dépôt en `failed` sans rien écrire, la règle « 3 échecs de paiement » du sujet n'avait rien à compter. Test `tests/api/test_us02_deposits.py::test_us02_6_refused_payment_logs_failure_with_user`.
- `auth.token_rejected` avec `reason=bad_signature` : avant, une signature forgée, un `alg: none`, un jeton expiré et un jeton mal formé donnaient tous `invalid`. Un jeton expiré, c'est du bruit, une signature fausse, c'est une tentative de forge. Test `tests/abuse/test_as01_account_takeover.py::test_as01_forged_signature_logged_apart_from_other_rejections`.
- `admin.access_revoked` : écrit par la nouvelle commande `toutbet-admin revoke-access` (voir `iam.md`).

Ce qui ne vient pas de l'application : la CPU (métriques du conteneur), les logs Caddy, PostgreSQL, PSP et GitHub.

## 2. Règles d'alerte

Sévérités :

- **critique** : argent ou intégrité en jeu, on réveille l'astreinte tout de suite ;
- **haute** : à traiter dans l'heure ;
- **moyenne** : à traiter dans la journée ouvrée.

Les fenêtres sont glissantes. Une règle qui s'est déclenchée repart de zéro, pour ne pas envoyer une alerte par ligne de log.

| Nom | Condition | Sévérité | Événement source | Réponse |
|---|---|---|---|---|
| `GRAND_LIVRE_INCOHERENT` | 1 `ledger.verify_failed`, ou code de sortie ≠ 0 de `verify-ledger` (tâche planifiée toutes les heures) | critique | `ledger.verify_failed` | Couper `STAKES_ENABLED`, `DEPOSITS_ENABLED`, `WITHDRAWALS_ENABLED`. Comparer avec les `ledger.head` déjà publiés pour dater l'écart |
| `TETE_GRAND_LIVRE_ABSENTE` | aucun `ledger.head` depuis 10 min, ou 1 `ledger.head_missing` | haute | `ledger.head`, `ledger.head_missing` | Le worker est arrêté ou bloqué : le relancer (scénario 3 de `chaos.md`) |
| `WORKER_ARRETE` | aucun `worker.tick` depuis 2 min, ou 3 `worker.tick_failed` de suite | haute | `worker.tick`, `worker.tick_failed` | Relancer le worker, regarder la trace du dernier échec |
| `WEBHOOK_PSP_SIGNATURE_INVALIDE` | 3 `psp.webhook_rejected` en 5 min, toutes IP confondues (1 seul : moyenne) | critique | `psp.webhook_rejected` | Comparer `client_ip` aux plages du PSP, bloquer l'IP au proxy. Si les requêtes viennent du PSP, le secret a peut-être changé ou fui : rotation de `PSP_WEBHOOK_SECRET` |
| `MONTANT_PSP_DIFFERENT` | 1 `psp.amount_mismatch` | critique | `psp.amount_mismatch` | Rien n'est crédité. Vérifier le dépôt chez le PSP, chercher une falsification du montant |
| `PAIEMENTS_REFUSES_EN_SERIE` | 3 `money.deposit_failed` de suite pour un même `user_id`, sans `money.deposit_credited` entre eux, en moins de 1 h | haute | `money.deposit_failed` | Exemple du sujet. Test de cartes volées probable : couper les dépôts de ce compte, prévenir le PSP |
| `PAIEMENTS_REFUSES_REPETES` | 5 `money.deposit_failed` pour un même `user_id` en 1 h, même s'il y a des dépôts réussis entre eux | haute | `money.deposit_failed` | Même réponse. Couvre celui qui glisse un petit dépôt réussi entre deux refus pour remettre la série à zéro |
| `FORCE_BRUTE_CONNEXION` | 20 réponses 401 ou 429 sur `/api/auth/login` depuis une même `client_ip` en 60 s | haute | `http.request` | La limite de 5/min et le verrou de compte tiennent déjà. Bloquer l'IP au proxy si ça dure, vérifier `auth.account_locked` |
| `BOURRAGE_IDENTIFIANTS` | 50 `auth.login_failed` en 5 min toutes IP confondues avec plus de 70 % de `reason=unknown_account`, ou `auth.account_locked` sur 3 comptes différents en 10 min | haute | `auth.login_failed`, `auth.account_locked` | Credential stuffing depuis beaucoup d'IP : la limite par IP ne suffit plus. Durcir la limite globale, captcha, prévenir les comptes verrouillés |
| `ATTAQUE_ET_CPU` | réponses 401 + 403 + 429 sur 5 min supérieures à 5 fois la moyenne des 7 derniers jours, et CPU du conteneur `app` au-dessus de 80 % sur la même fenêtre | haute | `http.request`, `rate_limit.exceeded`, métriques CPU | La corrélation du cours (hausse CPU et explosion des 403 = force brute). Regarder les routes et IP en tête, bloquer au proxy |
| `MODERATEUR_VERROUILLE` | 1 `auth.account_locked` dont le `user_id` est un modérateur | haute | `auth.account_locked` | Prévenir le modérateur par un autre canal, vérifier ses sessions, `revoke-access` si doute |
| `JWT_SIGNATURE_FORGEE` | 5 `auth.token_rejected` avec `reason=bad_signature` en 5 min (1 seul hors période de rotation de `JWT_SECRET` : moyenne) | haute | `auth.token_rejected` | Quelqu'un fabrique des jetons. Vérifier qu'aucun n'est passé (pas de 200 avec le même `client_ip`), envisager la rotation de `JWT_SECRET` |
| `BALAYAGE_IDOR` | 30 `authz.denied` pour un même `user_id` en 1 min | haute | `authz.denied` | Balayage d'identifiants : `revoke-access` sur le compte, relire ce qu'il a obtenu en 200 |
| `RETRAIT_NON_CONFIRME` | un même `withdrawal_id` encore dans `money.withdrawal_unconfirmed` 30 min après sa première apparition | haute | `money.withdrawal_unconfirmed` | Le PSP ne répond toujours pas pour ce virement. Vérifier chez le PSP, ne jamais relancer à la main sous une autre référence |
| `PSP_INDISPONIBLE` | plus de 5 % des appels au PSP en `psp.timeout` sur 5 min, ou 3 `money.withdrawal_unconfirmed` sur des retraits différents en 5 min | moyenne | `psp.timeout`, `money.withdrawal_unconfirmed` | Couper `DEPOSITS_ENABLED` et `WITHDRAWALS_ENABLED` si ça dure plus de 10 min, pour ne pas saturer l'API (voir `chaos.md`) |
| `RETRAIT_REFUSE` | 1 `money.withdrawal_failed` | moyenne | `money.withdrawal_failed` | Le solde est déjà recrédité. Vérifier la raison chez le PSP (compte fermé, fraude) |
| `ANOMALIE_SOLDE` | 10 `money.stake_rejected` ou `money.withdrawal_rejected` avec raison fonds insuffisants pour un même `user_id` en 1 min | moyenne | `money.stake_rejected`, `money.withdrawal_rejected` | Quelqu'un tente de dépenser deux fois en parallèle. Le débit conditionnel tient, lancer `verify-ledger` pour le confirmer |
| `DECISIONS_MODERATION` | chaque `moderation.decision` est notifiée au canal de l'équipe ; 5 décisions du même `moderator_id` en 1 h, ou une décision depuis une `client_ip` jamais vue pour ce modérateur : haute | moyenne, haute | `moderation.decision`, `http.request` | Relire la justification (audit chaîné). Scénario de la section 3 |
| `ACTION_ADMIN` | 1 `admin.moderator_created` ou `admin.access_revoked` | haute | `admin.*` | Doit correspondre à une demande écrite du Lead (JIT). Sinon, c'est une intrusion dans le conteneur |
| `RAFALE_5XX` | réponses 500 au-dessus de 1 % des requêtes sur 5 min (minimum 100 requêtes), ou 10 `error.unhandled` en 1 min. Les 503 voulus (`FEATURE_DISABLED`, `PSP_UNAVAILABLE`, `WITHDRAWAL_PENDING`) sont comptés à part | haute | `http.request`, `error.unhandled` | Objectif de `STEERING.md` : moins de 0,1 % de 5xx. Regarder la trace, rollback si c'est arrivé avec un déploiement |
| `LIMITEUR_DECLENCHE` | 30 `rate_limit.exceeded` pour une même IP ou un même `user_id` en 1 min, ou 300 en tout | moyenne | `rate_limit.exceeded` | Souvent un client buggé. Haute si `ATTAQUE_ET_CPU` est aussi levée |
| `CI_ROUGE_SUR_MAIN` | un job `Secret scan` ou `Semgrep OSS` en échec sur un push vers `main` (webhook GitHub `workflow_run`, `conclusion: failure`) | haute (critique pour gitleaks) | GitHub Actions | gitleaks rouge : le secret est peut-être déjà public, rotation d'abord, nettoyage ensuite. Semgrep rouge : corriger avant tout déploiement |
| `DEPENDABOT_CRITIQUE` | une alerte Dependabot de sévérité `critical` ouverte (webhook `dependabot_alert`, `action: created`), ou `high` avec un exploit connu | haute | GitHub Dependabot | Correction sous 7 jours (`STEERING.md`), en priorité si l'exploit circule |
| `DEPLOIEMENT_HORS_WORKFLOW` | un déploiement en production sans run du job `deploy` approuvé dans l'environnement `production`, une image en service dont le digest n'a pas d'attestation de la CI, ou une session `exec` sur la prod hors demande JIT | critique | journal d'audit GitHub (`deployment`, `deployment_review`), registre, bastion | Considérer la prod compromise : redéployer le dernier digest attesté, chercher qui et comment |

### Règles déjà évaluables

`toutbet-admin check-alerts <fichier de log>` relit les logs JSON et évalue cinq de ces règles : `GRAND_LIVRE_INCOHERENT`, `WEBHOOK_PSP_SIGNATURE_INVALIDE`, `PAIEMENTS_REFUSES_EN_SERIE`, `PAIEMENTS_REFUSES_REPETES` et `FORCE_BRUTE_CONNEXION`. Il écrit une ligne JSON par alerte et sort avec le code 1 s'il en a trouvé (`src/toutbet/alerts.py`). C'est une preuve que les règles tiennent sur nos vrais événements, pas un SIEM : en production on mettrait les mêmes conditions dans l'outil de logs (Loki, Elastic, Datadog).

Tests dans `tests/unit/test_alerts.py`, dont `test_real_refused_payments_and_forged_webhooks_raise_alerts`, qui passe par l'API (3 paiements refusés, 3 webhooks mal signés) puis évalue les logs réellement produits, et `test_refusals_hidden_between_small_successes_still_alert_within_an_hour` pour la règle par fenêtre.

## 3. Logs à croiser après une intrusion

Scénario : un jeton de modérateur est volé (logiciel espion sur son poste, ou XSS qui lit `sessionStorage`). L'attaquant prépare deux comptes complices qui misent sur NON, attend qu'un pari passe en litige, tranche NON avec une justification plausible, puis les complices retirent leurs gains. Le séquestre du pari part chez eux.

Ordre de lecture :

| # | Source | Ce qu'on y cherche | Clé de jointure |
|---|---|---|---|
| 1 | Alerte ou plainte | `DECISIONS_MODERATION` (IP jamais vue), ou des parieurs qui contestent la décision | `bet_id` |
| 2 | Journal d'audit chaîné (`audit_events`) | `moderation.decision` du pari : `actor_id`, justification, heure. `admin.*` récents | `bet_id`, `actor_id` = `user_id` du modérateur |
| 3 | Grand livre (`ledger_entries`) | écritures de règlement du pari : bénéficiaires, montants, `session_id`, `request_id` | `bet_id`, `request_id`, `session_id` |
| 4 | Logs applicatifs | `moderation.decision` puis la ligne `http.request` du même `request_id` : `client_ip`, `user_id`, heure, route | `request_id` |
| 5 | Logs d'authentification (même flux, événements `auth.*`) et table `sessions` | `auth.login_succeeded` du modérateur, création de la session utilisée, refresh, autres sessions ouvertes. IP de la connexion d'origine contre IP de la décision | `user_id`, `session_id` (claim `sid` du jeton), `request_id` vers `http.request` |
| 6 | Logs d'accès Caddy | même IP et même heure : User-Agent, empreinte TLS, `X-Forwarded-For`, autres chemins visités, ce que l'application ne journalise pas | `client_ip` + horodatage |
| 7 | Grand livre et reçus des complices | leurs mises : reçu signé (`ledger_hash`, `stake_id`), `request_id` de la mise, donc leur IP. Si c'est la même que celle de la décision, le lien est fait | `ledger_hash` du reçu = `hash` de l'écriture, `request_id` |
| 8 | Retraits et logs du PSP | `money.withdrawal_*` des complices, puis le virement chez le PSP : la référence du virement est l'id du retrait. Le PSP connaît le compte bancaire vérifié par KYC | `withdrawal_id` = référence PSP, `user_id` |
| 9 | Logs des webhooks PSP | `kyc.status_changed` et dépôts des complices : comptes créés et vérifiés juste avant l'attaque | `user_id`, `event_id` du PSP |
| 10 | Logs PostgreSQL | connexions et requêtes du rôle propriétaire (`log_connections`, pgaudit) pour écarter une modification directe de la base. `verify-ledger` confirme que la chaîne est intacte | horodatage, rôle |
| 11 | Journal d'audit GitHub et logs de la CI | changement de code ou déploiement hors workflow autour de la date, qui aurait affaibli l'autorisation | horodatage, digest d'image |
| 12 | Poste du modérateur (EDR) | comment le jeton est sorti | horodatage, IP |

Ce qui relie les sources :

- `request_id` : renvoyé dans `X-Request-ID`, présent sur chaque événement d'une requête et dans chaque écriture du grand livre ;
- `user_id` et `actor_id` : identifiant interne, jamais l'email ;
- `session_id` : dans l'écriture du grand livre et dans le jeton (`sid`), il dit quelle connexion a servi ;
- IP : `client_ip` de `http.request` et les logs Caddy ;
- horodatages en UTC partout (application, Caddy, PostgreSQL, GitHub) ;
- `hash` du grand livre : le reçu de mise porte `ledger_hash`, et `ledger.head` publié toutes les 5 min date l'existence d'une écriture ;
- id du retrait = référence et clé d'idempotence du virement chez le PSP.

Fin de l'enquête côté application : `toutbet-admin revoke-access --email <modérateur>`, `revoke-access` sur les complices, retraits bloqués chez le PSP si encore possible, décision rejouée par un autre modérateur.

## 4. Cartes et mots de passe hors des logs (RGPD, PCI-DSS)

Ce qui est déjà en place :

| Mesure | Où | Test |
|---|---|---|
| Aucune donnée de carte chez nous : la page de paiement est hébergée par le PSP (périmètre PCI-DSS SAQ A). L'API ne reçoit qu'un montant et renvoie une URL | `api/deposits.py`, `psp/client.py` | `tests/api/test_us02_deposits.py::test_us02_2_card_data_is_never_received_stored_or_logged`, `tests/unit/test_mock_psp.py::test_payment_page_has_no_card_field_and_strict_csp` |
| Logger structuré : chaque appel `log_event(event, **champs)` nomme ses champs, aucun corps de requête ni de réponse n'est journalisé, `http.request` garde le gabarit de route et pas l'URL brute | `observability.py`, `middleware/request_id.py` | `tests/security/test_logging.py::test_logs_are_json_lines_with_request_id` |
| Filtre de masquage sur les champs, les messages et les traces : clés contenant `password`, `secret`, `token`, `authorization`, `api_key`, `signature`, `card`, `pan`, `cvv`, `iban` remplacées par `***`, JWT et numéros de carte masqués, emails réduits à `a***@domaine` | `observability.py` (`RedactionFilter`) | `tests/unit/test_redaction.py` : `test_redaction_masks_card_numbers_and_jwts`, `test_redaction_keeps_hashes_and_identifiers_intact`, `test_redaction_masks_sensitive_keys_recursively`, `test_redaction_filter_covers_messages_fields_and_tracebacks` |
| `hide_parameters=True` sur tous les moteurs SQLAlchemy : une erreur SQL montre la requête, jamais les valeurs (date de naissance, hash, email) | `db.py` | `tests/abuse/test_as16_information_leak.py::test_as16_database_error_never_logs_bound_parameters` |
| Log d'accès d'uvicorn coupé (`--no-access-log` et `configure_logging`), il écrirait les URL brutes | `Dockerfile`, `observability.py` | `tests/security/test_logging.py::test_configure_logging_keeps_uvicorn_access_log_off` |
| Bout en bout : un parcours complet (dépôt, webhook, KYC, retraits avec bon et mauvais mot de passe) ne laisse ni mot de passe, ni jeton, ni secret, ni signature, ni email dans les logs | | `tests/abuse/test_as16_information_leak.py::test_as16_logs_never_contain_secrets` |
| Chaque run E2E relit le log du serveur et échoue s'il y trouve le mot de passe de test ou un JWT | `tests/e2e/conftest.py` (`_check_app_log`) | toute la suite E2E |
| Mots de passe hachés avec Argon2id. Au retrait, le mot de passe redemandé est exclu de l'empreinte d'idempotence, il n'est jamais stocké, même haché | `security/passwords.py`, `api/withdrawals.py` (`request_fingerprint` ne reçoit que `amount_cents`) | pas de test dédié pour l'empreinte, vu en revue (R1) |

Le masquage est un filet. Le vrai contrôle, c'est de ne jamais passer la donnée au logger. On a donc ajouté une 4e règle Semgrep maison, `toutbet-sensitive-data-in-logs` dans `.semgrep/toutbet.yml`. Elle signale :

- un argument nommé de `log_event` dont le nom ressemble à un secret ou à une carte (`password`, `token`, `secret`, `api_key`, `authorization`, `signature`, `card`, `cvv`, `iban`, `pan`) ;
- un argument de `log_event` dont la valeur est une telle variable ou un tel attribut (`detail=body.password`), les chaînes littérales comme `reason="bad_password"` restent permises ;
- une f-string passée à `log_event` ou à un logger (`logger.info(f"...")`), y compris comme valeur d'un champ ;
- un logger standard qui reçoit une variable sensible en argument (`logger.warning("... %s", password)`) ou dans `extra={"token": ...}`.

Exemples bons et mauvais dans `.semgrep/tests/toutbet.py` (14 alertes attendues, 7 cas qui ne doivent pas alerter). `semgrep --test` : 4/4 règles passent, et 0 alerte sur `src/`. Le job « Semgrep OSS » de la CI lance déjà `semgrep scan --test` puis le scan avec `.semgrep/toutbet.yml`, la règle y tourne sans changer `ci.yml`. Par défaut Semgrep ignore `tests/`, ce qui est voulu ici : `test_redaction.py` journalise un faux jeton exprès pour vérifier le masquage.

## 5. Conservation et accès aux logs

| Logs | Conservation | Qui y accède |
|---|---|---|
| Applicatifs (sortie standard JSON) | 90 jours (`STEERING.md`), puis suppression | développeurs en lecture sur 30 jours, ops et Lead sur 90 jours, auditeur en lecture |
| Sous-ensemble sécurité (`auth.*`, `authz.*`, `admin.*`, `moderation.*`, `psp.webhook_rejected`, `ledger.*`) | 1 an en stockage froid en lecture seule (la CNIL recommande 6 mois à 1 an pour la journalisation des accès) | ops et Lead sur demande, auditeur |
| Grand livre et journal d'audit chaînés | durée légale des écritures comptables (10 ans), en base, jamais dans les logs | application en ajout seul, enquête en JIT |
| Caddy (accès, contient les IP) | 90 jours | ops |
| PostgreSQL | 30 jours | ops |
| GitHub (journal d'audit, logs Actions) | GitHub ne garde le journal d'audit que 180 jours (7 jours pour les événements Git), export mensuel | Lead, auditeur |

Règles d'accès :

- l'application écrit ses logs mais ne peut ni les relire ni les effacer : ils partent vers un stockage en ajout seul (bucket avec verrouillage d'objets), c'est ce qui rend `ledger.head` utile ;
- les accès à l'outil de logs sont eux-mêmes journalisés, et l'export brut hors de l'outil est interdit ;
- les logs contiennent des données personnelles (IP, identifiant interne) : ils entrent dans le registre RGPD, avec la durée ci-dessus et la purge automatique ;
- si une donnée interdite arrive quand même dans les logs (bug), on purge les lignes concernées, on corrige, on ajoute un test, et si c'est un secret on le fait tourner.
