# Un même utilisateur qui mise beaucoup en même temps peut-il dépenser plus que son solde ?

Réponse : non. On l'a vérifié en HTTP sur PostgreSQL, puis on a retiré la protection pour montrer que le risque est bien réel.

## Pourquoi c'est impossible dans le code actuel

- **Débit conditionnel.** `services/money.py` débite en une seule requête : `UPDATE wallets SET balance_cents = balance_cents - :montant WHERE user_id = :id AND balance_cents >= :montant`. Le solde n'est jamais lu en Python puis réécrit. Si aucune ligne n'est modifiée (`rowcount != 1`), c'est `INSUFFICIENT_FUNDS` : la transaction entière est annulée, mise, écriture au grand livre et clé d'idempotence comprises, et l'API répond 409. Sous PostgreSQL, deux `UPDATE` sur le même portefeuille passent l'un après l'autre : le second attend le verrou de ligne puis réévalue sa condition sur le solde déjà débité.
- **Contrainte en base.** `CHECK (balance_cents >= 0)` sur `wallets` : même avec un bug dans le code, la base refuse un solde négatif.
- **Clés d'idempotence.** Chaque mise exige un `Idempotency-Key`. Une requête rejouée (double clic, réseau qui relance) renvoie la réponse enregistrée sans second débit, et la même clé avec un autre corps est refusée (422).
- **Une mise par participant et par pari** (`UNIQUE(bet_id, user_id)` sur `stakes`).
- **Contrôle a posteriori.** `toutbet-admin verify-ledger` vérifie que chaque solde est égal à la somme des écritures du grand livre.

## Le test

`tests/load/test_live_overspend.py`, lancé contre la pile du test de charge (PostgreSQL 17 avec le rôle restreint, uvicorn 4 workers, limites de débit relevées pour que seul le code de l'argent fasse barrage) :

```sh
TOUTBET_LIVE_URL=http://127.0.0.1:8200 \
TOUTBET_LIVE_DATABASE_URL=postgresql+psycopg://toutbet_app:app-load-only@127.0.0.1:55433/toutbet \
uv run --group load pytest tests/load/test_live_overspend.py -s
```

Un joueur dépose 50 € par le PSP simulé et rejoint 50 paris. Avec asyncio et httpx, il envoie ses 50 mises de 7 € en même temps, une par pari. Il peut en payer floor(5 000 / 700) = 7, et il doit lui rester 1 €. Le test vérifie le nombre de mises acceptées, le nombre de mises enregistrées sur les paris, le solde final, l'absence de 5xx, et lance `toutbet-admin verify-ledger`.

## Résultats

| Version de `_debit_wallet` | Lancements | Acceptées | Refusées | Solde final | `verify-ledger` |
|---|---|---|---|---|---|
| actuelle (`UPDATE ... WHERE balance_cents >= :montant`) | 5, puis 3 après restauration | 7 | 43 x 409 `INSUFFICIENT_FUNDS` | 1,00 € | OK |
| naïve A : lecture du solde, contrôle en Python, écriture de la nouvelle valeur | 3 | 50 | 0 | 36,00 € | échec : « balance 3600 differs from ledger total -30000 » |
| naïve B : lecture du solde, contrôle en Python, `balance_cents = balance_cents - :montant` | 1 | 7 | 20 x 409 et 23 x 500 | 1,00 € | OK |

Les deux versions naïves ont été écrites dans un `git worktree` jetable, jamais commité, et l'application a été lancée depuis ce worktree sur une base neuve à chaque fois :

```python
balance = db.scalar(select(Wallet.balance_cents).where(Wallet.user_id == user_id))
if balance is None or balance < amount_cents:
    raise InsufficientFundsError
db.execute(
    update(Wallet).where(Wallet.user_id == user_id).values(balance_cents=balance - amount_cents)
)
# variante B : .values(balance_cents=Wallet.balance_cents - amount_cents)
```

**Version A, le risque réel.** Les 50 transactions lisent 50 € avant que la première ne valide. Chacune écrit « 50 € - 7 € », la suivante écrase la précédente (mise à jour perdue). Résultat : 50 mises acceptées, soit 350 € misés avec 50 €, et le portefeuille affiche encore 36 €. Le joueur a dépensé 300 € qu'il n'avait pas, et le solde reste positif, donc la contrainte `CHECK` ne voit rien. Seul `verify-ledger` détecte l'écart : le grand livre dit -300 €. Même résultat aux 3 lancements.

**Version B, la contrainte sauve l'argent mais pas l'utilisateur.** Le contrôle en Python laisse passer des mises qui ont lu un solde périmé, mais le décrément est relatif : chaque validation retire bien 7 €. Celles qui feraient passer le solde sous zéro sont arrêtées par `CHECK (balance_cents >= 0)` : `IntegrityError`, transaction annulée, réponse 500. L'argent est juste (7 mises, 1 € restant, grand livre cohérent), mais 23 joueurs reçoivent une erreur interne au lieu d'un 409 propre.

Après les essais, le worktree a été supprimé, la base recréée et la version actuelle relancée : 3 lancements sur 3 donnent 7 mises acceptées, 1 € restant et `verify-ledger` OK.

## Ce qu'on en retient

La protection tient en une ligne (la condition dans le `UPDATE` et la vérification du `rowcount`), et c'est elle qui fait tout le travail. La contrainte `CHECK` est un bon filet, mais seule elle ne suffit pas : contre la version A elle ne se déclenche jamais. C'est pour ça que la checklist de revue R1 imposait déjà de vérifier que seul `services/money.py` écrit `balance_cents`, et qu'en R1 on avait retiré `balance_cents >= amount` pour s'assurer qu'un test cassait.

Les tests de charge confirment le même résultat à plus grande échelle : 5 160 mises sur quatre runs, dont une rafale de 3 000 mises sur un même pari, et `verify-ledger` passe à chaque fois.
