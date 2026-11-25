# Règles Semgrep personnalisées

Le sujet demande de proposer deux ou trois règles propres à ToutBet' sans les écrire. On en propose trois, et on est allé un peu plus loin : elles sont écrites dans `.semgrep/toutbet.yml`, avec des exemples bons et mauvais dans `.semgrep/tests/toutbet.py` vérifiés par `semgrep --test`.

## 1. Écriture directe d'un solde hors du module monétaire

Identifiant : `toutbet-balance-write-outside-money`. Menaces : T-T02, T-T08, T-T10.

Risque : un solde modifié en dehors de `transfer()` n'a pas d'écriture dans le grand livre chaîné, pas de contrôle `balance_cents >= montant` et pas de transaction commune avec le séquestre. On peut créer de l'argent, passer en négatif sous concurrence, ou casser `verify-ledger` sans que personne ne sache d'où vient l'écart.

Critères concrets, alerte si l'un d'eux apparaît dans un fichier autre que `src/toutbet/services/money.py` :

- affectation `x.balance_cents = ...`, `+=` ou `-=` ;
- `.values(balance_cents=...)` ou `.values({"balance_cents": ...})` sur une requête (`update(Wallet)`) ;
- `Wallet(..., balance_cents=v)` avec `v` différent de `0` (ouvrir un portefeuille vide reste permis) ;
- SQL brut qui contient `UPDATE wallets` ou `INSERT INTO wallets` dans `text()`, `execute()` ou `exec_driver_sql()`.

## 2. Montant manipulé en flottant ou en Decimal

Identifiant : `toutbet-money-float`. Menaces : T-T04, T-T10.

Risque : l'argent est en centimes entiers et les taux en points de base. Un `float` arrondit (0,1 + 0,2), un `/` Python renvoie toujours un `float`, et le règlement ne conserverait plus la somme des mises : des centimes apparaissent ou disparaissent à chaque pari.

Critères concrets, sur toute variable ou attribut dont le nom contient `cents`, `bps`, `amount`, `balance`, `stake`, `fee`, `commission`, `payout` ou `price` :

- annotation `float`, `Decimal`, `Mapped[float]` ou `Mapped[Decimal]` (avec ou sans `| None`) ;
- appel `float(...)` ou `Decimal(...)` sur cette valeur ;
- division `/` dont un côté est un montant (ajout de `total` et `pool` pour les cagnottes) ;
- multiplication d'un montant par un littéral décimal (`pool_cents * 0.05`).

Le critère repose sur le nommage, c'est sa limite : un montant mal nommé passe. `worker.py` garde un `float` pour une horloge monotone (`last_head`), il n'est pas signalé.

## 3. Route sans dépendance d'authentification

Identifiant : `toutbet-route-without-auth`. Menaces : T-E02, T-I01.

Risque : une route ajoutée sur un routeur non protégé est publique par défaut. C'est le cas classique de la route d'admin ou de debug oubliée, qui donne accès aux paris ou aux portefeuilles des autres.

Critères concrets, alerte sur toute fonction décorée `@router.get/post/put/patch/delete` quand :

- le routeur vient de `APIRouter(...)` ou `FastAPI(...)` et non de `protected_router(...)`, et ne déclare pas `dependencies=[Depends(current_user)]` ;
- la fonction ne prend aucun paramètre `AuthenticatedUser`, `Player` ou `Moderator` ;
- la fonction n'est pas un des cinq handlers publics de `PUBLIC_ROUTES` (`register_account`, `login`, `psp_webhook`, `public_key`, `healthz`).

Ajouter une route publique oblige donc à modifier la règle, ce qui force une revue. La liste blanche est par nom de fonction, pas par chemin, parce que Semgrep ne sait pas recoller le préfixe du routeur et le chemin de la route.

## Résultats

| Règle | Exemples `--test` (alerte / pas d'alerte) | `src/` | `src/` + `tests/` |
|---|---|---|---|
| `toutbet-balance-write-outside-money` | 8 / 4 | 0 | 3 |
| `toutbet-money-float` | 10 / 6 | 0 | 0 |
| `toutbet-route-without-auth` | 3 / 6 | 0 | 0 |

`semgrep --test --config .semgrep/toutbet.yml .semgrep/tests/toutbet.py` : 3/3 règles passent.

Zéro alerte sur `src/`, ce qui confirme la revue R1. Pour vérifier que les règles voient bien le vrai code et pas seulement nos exemples, on les a relancées sans leurs exceptions : la règle 1 trouve alors les deux `update(Wallet)` de `services/money.py` (et ignore bien `Wallet(..., balance_cents=0)`), la règle 3 trouve exactement les cinq routes de `PUBLIC_ROUTES`.

Les trois alertes dans `tests/` sont voulues : `test_cli.py`, `test_db_constraints.py` et `test_ledger.py` falsifient un solde en base pour prouver que `verify-ledger` et la contrainte `CHECK (balance_cents >= 0)` détectent la fraude. Sortie complète dans `regles-personnalisees.json`.

Ces règles doublent deux tests AST qui existaient déjà (`tests/unit/test_money.py`) et le test dynamique des routes de l'AS-12. L'intérêt est d'avoir l'alerte dans l'outil de scan au moment de la revue de code, et la règle 1 couvre en plus le SQL brut, que le test AST ne voit pas.

## Lancer

```sh
semgrep --test --config .semgrep/toutbet.yml .semgrep/tests/toutbet.py
semgrep scan --config .semgrep/toutbet.yml src
```

Attention, `semgrep --test .semgrep/` ne teste rien : Semgrep ignore les fichiers de règles rangés dans un dossier qui commence par un point, il faut passer le fichier.

## Idées écartées

- `jwt.decode` sans `algorithms`, `audience` ou `issuer` : utile, mais déjà couvert en partie par les règles JWT publiques et par `test_as01_tampered_token_rejected`.
- `log_event` appelé avec un argument `password`, `token` ou `secret` : le filtre de masquage remplace déjà ces clés par `***`, la règle n'aurait rien ajouté de concret.
