# ToutBet' : exigences du MVP

Version 1.0 du 2026-11-25. Backlog de l'atelier 1 : 12 user stories (`US-xx`), chacune avec sa version malveillante (`AS-xx`), plus 4 abuse stories transverses.

## Conventions

Les critères d'acceptation suivent la syntaxe EARS pour être testables sans interprétation :

| Motif | Forme |
|---|---|
| Permanent | LE SYSTÈME DOIT ... |
| Événement | QUAND <déclencheur>, LE SYSTÈME DOIT ... |
| État | TANT QUE <état>, LE SYSTÈME DOIT ... |
| Comportement indésirable | SI <condition>, ALORS LE SYSTÈME DOIT ... |

- Chaque critère a un identifiant (`US-07.3`) repris dans le nom du test (`test_us07_3_...`).
- Chaque abuse story a un fichier de test `tests/abuse/test_asXX_<sujet>.py` et liste les menaces de `threats.md` qu'elle couvre.
- Les montants sont en centimes d'euro (entiers), les durées en heure serveur UTC.
- Les codes d'erreur (`BET_CLOSED`, ...) et le format de réponse sont définis dans `design.md` §9.

## Synthèse

| User story | Abuse story | Menaces |
|---|---|---|
| US-01 Créer un compte et se connecter | AS-01 Prise de contrôle par force brute | T-S01, T-S02, T-I04, T-D02 |
| US-02 Approvisionner son portefeuille | AS-02 Créditer son compte sans payer | T-S03, T-T03 |
| US-03 Consulter son solde et son historique | AS-03 Espionner le portefeuille d'un autre | T-I01, T-I07 |
| US-04 Vérifier son identité et retirer | AS-04 Encaisser avec un compte volé | T-S04, T-S05, T-T02 |
| US-05 Créer un pari | AS-05 Truquer les paramètres du pari | T-T04, T-T06, T-T09 |
| US-06 Inviter des parieurs | AS-06 Entrer dans un pari privé | T-E03, T-I01 |
| US-07 Miser sur une issue | AS-07 Modifier sa mise ou dépenser plus que son solde | T-T01, T-T02, T-T03 |
| US-08 Clôturer avant l'événement | AS-08 Parier en connaissant le résultat | T-T06 |
| US-09 Déclarer le résultat | AS-09 Mentir sur le résultat | T-T05, T-R02, T-E02, T-E05, T-D06 |
| US-10 Contester un résultat | AS-10 Bloquer le paiement par des contestations | T-D04, T-S06 |
| US-11 Recevoir ses gains | AS-11 Être payé deux fois | T-T10, T-T08 |
| US-12 Arbitrer un litige | AS-12 Se promouvoir modérateur | T-E01, T-E02, T-S02, T-T04 |
| transverse | AS-13 Saturer la plateforme pendant un grand match | T-D01, T-D02, T-D03 |
| transverse | AS-14 Collusion bookie, parieurs, modérateur | T-E04, T-R03, T-S06 |
| transverse | AS-15 Nier avoir parié | T-R01, T-R04, T-T08 |
| transverse | AS-16 Faire parler l'application | T-I02, T-I03, T-I08, T-T07 |

---

## US-01 Créer un compte et se connecter

> En tant que visiteur, je veux créer un compte et me connecter pour parier avec mes amis.

- US-01.1 QUAND un visiteur envoie un email valide, un pseudonyme de 3 à 30 caractères `[A-Za-z0-9_-]`, un mot de passe de 12 à 128 caractères et une date de naissance attestant 18 ans révolus, LE SYSTÈME DOIT créer le compte avec le rôle `user`, un portefeuille à 0 et le mot de passe haché en argon2id.
- US-01.2 SI l'email est déjà utilisé, ALORS LE SYSTÈME DOIT renvoyer exactement la même réponse qu'une inscription acceptée (202) et effectuer le même calcul de hash.
- US-01.3 QUAND un utilisateur se connecte avec des identifiants valides, LE SYSTÈME DOIT ouvrir une session serveur et renvoyer un jeton d'accès JWT valable 15 minutes.
- US-01.4 SI les identifiants sont invalides, ALORS LE SYSTÈME DOIT renvoyer 401 avec le même message, que l'email existe ou non.
- US-01.5 QUAND un utilisateur se déconnecte, LE SYSTÈME DOIT révoquer la session, et tout jeton de cette session DOIT ensuite être refusé (401).
- US-01.6 TANT QUE la session a moins de 8 h et n'est pas révoquée, LE SYSTÈME DOIT permettre de renouveler le jeton, l'ancien jeton devenant invalide.

### AS-01 Prendre le contrôle d'un compte par force brute

> En tant qu'attaquant, je veux tester des milliers de mots de passe issus de fuites sur la connexion pour vider le portefeuille d'un parieur.

- SI une même IP dépasse 5 tentatives de connexion par minute, ALORS LE SYSTÈME DOIT répondre 429 avec `Retry-After`, avant tout calcul argon2.
- SI l'attaquant fait varier l'en-tête `X-Forwarded-For` depuis une adresse qui n'est pas un proxy de confiance, ALORS LE SYSTÈME DOIT continuer à compter ses tentatives sur son adresse réelle.
- SI un compte cumule 10 échecs en 15 minutes, ALORS LE SYSTÈME DOIT le verrouiller 15 minutes sans changer la réponse renvoyée, et journaliser `auth.account_locked`.
- SI un jeton est altéré (signature fausse, `alg: none`, autre algorithme, `exp` dépassé, claim manquant), ALORS LE SYSTÈME DOIT renvoyer 401.
- SI un mot de passe dépasse 128 caractères, ALORS LE SYSTÈME DOIT le refuser (422) sans le hacher.

Menaces : T-S01, T-S02, T-I04, T-D02. Test : `tests/abuse/test_as01_account_takeover.py`.

---

## US-02 Approvisionner son portefeuille

> En tant que parieur, je veux déposer 20 € par carte pour pouvoir miser.

- US-02.1 QUAND un parieur demande un dépôt de 500 à 50 000 centimes avec un en-tête `Idempotency-Key`, LE SYSTÈME DOIT créer un dépôt `pending` et renvoyer l'URL de la page de paiement hébergée par le PSP.
- US-02.2 LE SYSTÈME DOIT ne jamais recevoir, stocker ni journaliser de données de carte.
- US-02.3 QUAND le PSP envoie un webhook `payment.succeeded` correctement signé dont le montant et la devise correspondent à un dépôt `pending`, LE SYSTÈME DOIT créditer le portefeuille et écrire l'écriture au grand livre, une seule fois.
- US-02.4 TANT QUE le webhook n'est pas reçu, LE SYSTÈME DOIT présenter le dépôt comme `pending`, même si le navigateur revient de la page du PSP.
- US-02.5 SI le PSP ne répond pas dans le délai (2 s connexion, 5 s lecture), ALORS LE SYSTÈME DOIT marquer le dépôt `failed` et renvoyer 503 `PSP_UNAVAILABLE` sans bloquer les autres requêtes.

### AS-02 Créditer son compte sans payer

> En tant que fraudeur, je veux forger ou rejouer un webhook de paiement pour créditer mon portefeuille sans payer.

- SI la signature est absente, fausse ou horodatée de plus de 5 minutes, ALORS LE SYSTÈME DOIT renvoyer 400 générique, ne rien créditer et journaliser `psp.webhook_rejected`.
- SI un webhook valide est rejoué (même `event_id`), ALORS LE SYSTÈME DOIT renvoyer 200 sans second crédit.
- SI le montant ou la devise diffère du dépôt `pending`, ALORS LE SYSTÈME DOIT ne rien créditer et journaliser `psp.amount_mismatch`.

Menaces : T-S03, T-T03. Test : `tests/abuse/test_as02_forged_webhook.py`.

---

## US-03 Consulter son solde et son historique

> En tant que parieur, je veux voir mon solde et l'historique de mes mouvements pour vérifier mes gains.

- US-03.1 QUAND un utilisateur consulte son portefeuille, LE SYSTÈME DOIT renvoyer son solde et ses écritures du grand livre, paginées par 50 au plus.
- US-03.2 LE SYSTÈME DOIT déterminer le portefeuille à partir de l'identité portée par le jeton uniquement, jamais d'un identifiant fourni par le client.
- US-03.3 QUAND un utilisateur consulte un de ses dépôts, LE SYSTÈME DOIT renvoyer son statut.
- US-03.4 LE SYSTÈME DOIT garantir que chaque solde est égal à la somme des écritures du compte, ce que vérifie la commande `toutbet-admin verify-ledger`.

### AS-03 Espionner le portefeuille d'un autre

> En tant que parieur curieux, je veux changer l'identifiant dans l'URL pour voir les dépôts, les mises et l'historique d'un autre joueur.

- SI un utilisateur demande un dépôt, un pari ou une mise qui ne le concerne pas, ALORS LE SYSTÈME DOIT renvoyer 404, sans révéler si la ressource existe.
- LE SYSTÈME DOIT ne jamais renvoyer le hash de mot de passe, l'email, le statut KYC ni les mises individuelles d'un autre utilisateur.

Menaces : T-I01, T-I07. Test : `tests/abuse/test_as03_wallet_idor.py`.

---

## US-04 Vérifier son identité et retirer ses gains

> En tant que parieur, je veux faire vérifier mon identité puis retirer mes gains sur mon compte bancaire.

- US-04.1 QUAND un parieur lance la vérification d'identité, LE SYSTÈME DOIT renvoyer l'URL de la page KYC hébergée par le PSP, sans jamais recevoir de pièce d'identité.
- US-04.2 QUAND le PSP envoie un webhook `kyc.verified` correctement signé, LE SYSTÈME DOIT passer le statut KYC du compte à `verified`.
- US-04.3 QUAND un parieur `verified` demande un retrait de 1 000 à 100 000 centimes avec `Idempotency-Key` et son mot de passe, LE SYSTÈME DOIT débiter le portefeuille de façon atomique et ordonner au PSP un virement vers le compte bancaire vérifié chez le PSP.
- US-04.4 SI le statut KYC n'est pas `verified`, ALORS LE SYSTÈME DOIT refuser le retrait (403 `KYC_REQUIRED`).
- US-04.5 SI le PSP refuse ou ne répond pas, ALORS LE SYSTÈME DOIT recréditer le portefeuille par une écriture compensatoire et marquer le retrait `failed`.

### AS-04 Encaisser avec un compte volé

> En tant qu'escroc qui a volé un jeton ou un compte, je veux retirer le solde vers mon propre compte bancaire.

- SI le mot de passe n'est pas ressaisi ou est faux, ALORS LE SYSTÈME DOIT renvoyer 401 sans rien débiter.
- SI la requête de retrait contient un IBAN ou tout autre champ de destination, ALORS LE SYSTÈME DOIT la refuser (422) : seul le compte vérifié chez le PSP peut recevoir les fonds.
- SI un utilisateur tente de passer lui-même son statut KYC à `verified` (champ dans un corps de requête, webhook non signé), ALORS LE SYSTÈME DOIT refuser et laisser le statut inchangé.
- SI des retraits concurrents dépassent le solde, ALORS LE SYSTÈME DOIT n'accepter que ceux couverts par le solde.

Menaces : T-S04, T-S05, T-T02. Test : `tests/abuse/test_as04_fraudulent_withdrawal.py`.

---

## US-05 Créer un pari

> En tant que bookie, je veux créer le pari « Matis va-t-il réussir son créneau ? » avec une mise minimum et ma commission pour animer mon groupe d'amis.

- US-05.1 QUAND un utilisateur crée un pari avec un titre de 5 à 140 caractères, une description optionnelle de 1 000 caractères au plus, une date de début d'événement, une échéance de clôture, une commission bookie de 0 à 1 000 points de base et une mise minimum optionnelle de 100 à 50 000 centimes, LE SYSTÈME DOIT créer le pari `open`, avec cet utilisateur comme bookie et une commission plateforme figée à 500 points de base.
- US-05.2 SI l'échéance est à moins de 5 minutes, postérieure au début de l'événement, ou si l'événement est à plus de 90 jours, ALORS LE SYSTÈME DOIT refuser (422).
- US-05.3 LE SYSTÈME DOIT proposer exactement deux issues : `OUI` et `NON`.
- US-05.4 LE SYSTÈME DOIT rendre les paramètres du pari (dates, commission, mise minimum, titre) non modifiables après création.
- US-05.5 QUAND le bookie annule un pari `open`, LE SYSTÈME DOIT le passer à `cancelled` et rembourser intégralement chaque mise.
- US-05.6 SI un modérateur tente de créer un pari, ALORS LE SYSTÈME DOIT refuser (403).

### AS-05 Truquer les paramètres du pari

> En tant que bookie malveillant, je veux m'attribuer 100 % de commission, déplacer l'échéance après coup ou glisser un script dans le titre pour capter l'argent des parieurs.

- SI la commission bookie est hors de 0 à 1 000, ou si un montant est négatif, décimal, booléen ou fourni en chaîne, ALORS LE SYSTÈME DOIT refuser (422).
- SI le corps contient un champ non prévu (`bookie_id`, `status`, `platform_commission_bps`, ...), ALORS LE SYSTÈME DOIT refuser (422).
- SI une requête `PUT`, `PATCH` ou `DELETE` vise un pari, ALORS LE SYSTÈME DOIT répondre 405.
- SI le titre contient du HTML ou du JavaScript, ALORS LE SYSTÈME DOIT le stocker tel quel, le front DOIT l'afficher comme texte, et la CSP DOIT interdire tout script inline.

Menaces : T-T04, T-T06, T-T09. Test : `tests/abuse/test_as05_bet_tampering.py`.

---

## US-06 Inviter des parieurs

> En tant que bookie, je veux envoyer un lien d'invitation à mes amis pour qu'ils rejoignent mon pari.

- US-06.1 QUAND le bookie demande une invitation pour son pari `open`, LE SYSTÈME DOIT générer un jeton aléatoire de 256 bits, n'en stocker que le hash SHA-256, et renvoyer un lien `/#/invite/<jeton>` valable 48 h.
- US-06.2 QUAND un utilisateur connecté accepte une invitation valide, LE SYSTÈME DOIT l'ajouter aux participants et marquer le jeton comme utilisé, dans la même opération atomique.
- US-06.3 SI le jeton est inconnu, expiré, déjà utilisé, ou si le pari n'est plus `open`, ALORS LE SYSTÈME DOIT renvoyer 404 avec le même message.
- US-06.4 SI un pari a déjà 50 invitations actives, ALORS LE SYSTÈME DOIT refuser d'en créer une nouvelle (409).
- US-06.5 LE SYSTÈME DOIT réserver la lecture d'un pari à son bookie et à ses participants : aucun pari n'est listé publiquement.

### AS-06 Entrer dans un pari privé sans y être invité

> En tant que parieur malveillant, je veux deviner ou réutiliser un lien d'invitation, ou appeler directement `/api/bets/<id>`, pour miser sur un pari dont je connais déjà l'issue.

- SI un utilisateur dépasse 10 tentatives d'acceptation d'invitation par minute, ALORS LE SYSTÈME DOIT répondre 429.
- SI un jeton déjà utilisé est présenté par un autre compte, ALORS LE SYSTÈME DOIT renvoyer 404 sans rien modifier.
- SI un non-participant lit un pari ou tente d'y miser, ALORS LE SYSTÈME DOIT renvoyer 404.

Menaces : T-E03, T-I01. Test : `tests/abuse/test_as06_private_bet_intrusion.py`.

---

## US-07 Miser sur une issue

> En tant que parieur, je veux miser 10 € sur « OUI » pour montrer que j'ai raison.

- US-07.1 QUAND un participant mise sur `OUI` ou `NON` un montant d'au moins 100 centimes, d'au moins la mise minimum du pari et d'au plus 50 000 centimes, sur un pari `open` avant `closes_at`, avec `Idempotency-Key`, LE SYSTÈME DOIT, dans une seule transaction, débiter le portefeuille par une mise à jour conditionnelle, créditer le séquestre du pari, enregistrer la mise et chaîner l'écriture au grand livre.
- US-07.2 QUAND la mise est enregistrée, LE SYSTÈME DOIT renvoyer un reçu signé (Ed25519) contenant l'identifiant de la mise, le pari, l'issue, le montant, l'horodatage et le hash de l'écriture.
- US-07.3 SI le solde est insuffisant, ALORS LE SYSTÈME DOIT refuser (409 `INSUFFICIENT_FUNDS`) sans rien débiter.
- US-07.4 LE SYSTÈME DOIT n'accepter qu'une mise par participant et par pari, et cette mise est définitive.
- US-07.5 SI le bookie du pari ou un modérateur tente de miser, ALORS LE SYSTÈME DOIT refuser (403).

### AS-07 Modifier sa mise après la clôture ou dépenser plus que son solde

> En tant que parieur malveillant, je veux modifier mon pari après sa clôture pour remporter la mise, ou envoyer des dizaines de mises simultanées pour dépenser plus que mon solde.

- SI une mise arrive à `closes_at` ou après, même si le planificateur n'a pas encore clôturé le pari, ALORS LE SYSTÈME DOIT refuser (409 `BET_CLOSED`).
- SI une requête `PUT`, `PATCH` ou `DELETE` vise une mise, ALORS LE SYSTÈME DOIT répondre 405.
- SI un utilisateur avec 100 € envoie en parallèle 50 mises de 10 € sur 50 paris différents, ALORS LE SYSTÈME DOIT en accepter exactement 10 et le solde final DOIT valoir 0.
- SI une requête est rejouée avec la même `Idempotency-Key`, ALORS LE SYSTÈME DOIT renvoyer la réponse initiale sans second débit. SI la même clé arrive avec un corps différent, ALORS LE SYSTÈME DOIT refuser (422 `IDEMPOTENCY_KEY_REUSED`).

Menaces : T-T01, T-T02, T-T03. Test : `tests/abuse/test_as07_stake_tampering.py`.

---

## US-08 Clôturer les mises avant l'événement

> En tant que parieur, je veux que les mises soient fermées avant le début de l'événement pour que personne ne parie en connaissant déjà l'issue.

- US-08.1 QUAND `closes_at` est atteinte, LE SYSTÈME DOIT passer le pari à `closed` (planificateur, toutes les 30 s).
- US-08.2 LE SYSTÈME DOIT vérifier, au moment de chaque mise et avec l'horloge serveur, que l'instant courant est antérieur à `closes_at`, sans dépendre du passage du planificateur.
- US-08.3 SI aucun résultat n'est proposé 72 h après `event_starts_at`, ALORS LE SYSTÈME DOIT passer le pari à `disputed` pour qu'un modérateur tranche.

### AS-08 Parier en connaissant déjà le résultat

> En tant que parieur complice du bookie, je veux miser après le début de l'événement ou faire repousser l'échéance pour parier à coup sûr.

- SI la requête de mise contient un horodatage fourni par le client, ALORS LE SYSTÈME DOIT la refuser (422) : seule l'horloge serveur compte.
- SI une mise arrive à `closes_at` ou après, ALORS LE SYSTÈME DOIT refuser (409 `BET_CLOSED`).
- LE SYSTÈME DOIT n'exposer aucune route qui modifie `closes_at` ou `event_starts_at`.

Menaces : T-T06. Test : `tests/abuse/test_as08_past_posting.py`.

---

## US-09 Déclarer le résultat

> En tant que bookie, je veux déclarer le résultat de mon pari pour que les gagnants soient payés.

- US-09.1 QUAND le bookie propose `OUI`, `NON` ou `ANNULE` sur son pari `closed`, après `event_starts_at`, avec un justificatif texte optionnel de 500 caractères au plus, LE SYSTÈME DOIT passer le pari à `result_proposed` et ouvrir une fenêtre de contestation de 24 h.
- US-09.2 LE SYSTÈME DOIT enregistrer la proposition une seule fois, sans possibilité de la modifier, et l'inscrire au journal d'audit chaîné.
- US-09.3 SI un participant autre que le bookie propose un résultat, ALORS LE SYSTÈME DOIT refuser (403). SI un non-participant essaie, ALORS LE SYSTÈME DOIT renvoyer 404.
- US-09.4 SI le pari n'est pas `closed` ou si l'événement n'a pas commencé, ALORS LE SYSTÈME DOIT refuser (409 `INVALID_STATE`).
- US-09.5 LE SYSTÈME DOIT traiter le justificatif comme du texte : jamais téléchargé, jamais interprété, même s'il contient une URL.

### AS-09 Mentir sur le résultat

> En tant que bookie malhonnête, je veux déclarer un faux résultat, ou ne jamais en déclarer, pour faire gagner mon complice ou bloquer l'argent des autres.

- SI le bookie propose un second résultat, ALORS LE SYSTÈME DOIT refuser (409).
- SI un modérateur infirme le résultat proposé, ALORS LE SYSTÈME DOIT annuler la commission du bookie et la redistribuer aux gagnants.
- SI le justificatif contient une URL, ALORS LE SYSTÈME DOIT ne faire aucune requête sortante vers cette URL.
- SI le bookie ne propose rien dans les 72 h après le début de l'événement, ALORS LE SYSTÈME DOIT escalader le pari en `disputed`.

Menaces : T-T05, T-R02, T-E02, T-E05, T-D06. Test : `tests/abuse/test_as09_false_result.py`.

---

## US-10 Contester un résultat

> En tant que parieur, je veux contester pendant 24 h un résultat que je pense faux pour qu'un modérateur tranche.

- US-10.1 TANT QUE la fenêtre de contestation est ouverte, LE SYSTÈME DOIT accepter une seule contestation par participant ayant misé, avec un motif de 10 à 500 caractères.
- US-10.2 QUAND la somme des mises des contestataires atteint 10 % du total misé sur le pari, LE SYSTÈME DOIT passer le pari à `disputed` et suspendre le règlement.
- US-10.3 SI la fenêtre est close, ALORS LE SYSTÈME DOIT refuser (409 `CONTEST_WINDOW_CLOSED`).
- US-10.4 QUAND la fenêtre se termine sans atteindre le seuil, LE SYSTÈME DOIT régler le pari selon le résultat proposé (US-11).

### AS-10 Bloquer le paiement par des contestations abusives

> En tant que perdant de mauvaise foi, je veux créer des faux comptes et contester en masse pour bloquer indéfiniment le paiement des gagnants.

- SI un utilisateur sans mise sur le pari conteste, ALORS LE SYSTÈME DOIT refuser (403, ou 404 s'il n'est pas participant).
- SI un participant conteste une seconde fois, ALORS LE SYSTÈME DOIT refuser (409 `ALREADY_CONTESTED`).
- LE SYSTÈME DOIT pondérer le seuil par les montants misés : dix comptes à 1 € ne suffisent pas à bloquer un pari de 500 €.
- SI une même IP crée plus de 5 comptes par heure, ALORS LE SYSTÈME DOIT répondre 429.

Menaces : T-D04, T-S06. Test : `tests/abuse/test_as10_contest_flood.py`.

---

## US-11 Recevoir ses gains

> En tant que parieur gagnant, je veux être payé automatiquement à la fin de la fenêtre de contestation.

- US-11.1 QUAND un pari est réglé, LE SYSTÈME DOIT, en entiers et dans une seule transaction, prélever la commission plateforme puis la commission bookie sur le pot perdant, répartir le reste entre les gagnants au prorata de leur mise (méthode du plus fort reste, `design.md` §7), et créditer chaque gagnant de sa mise plus sa part.
- US-11.2 SI le résultat est `ANNULE`, ou si personne n'a misé sur l'une des deux issues, ALORS LE SYSTÈME DOIT rembourser chaque mise intégralement, sans commission.
- US-11.3 LE SYSTÈME DOIT garantir que la somme des crédits et des commissions est égale au total misé, et que le séquestre du pari finit à 0.
- US-11.4 LE SYSTÈME DOIT régler un pari au plus une fois.

### AS-11 Être payé deux fois

> En tant que parieur malveillant, je veux déclencher deux fois le règlement (requêtes simultanées, double passage du planificateur) ou exploiter les arrondis pour toucher plus que ma part.

- SI deux règlements du même pari s'exécutent en parallèle, ALORS LE SYSTÈME DOIT n'en appliquer qu'un.
- LE SYSTÈME DOIT n'exposer aucune route HTTP qui déclenche un règlement : seuls le planificateur et la décision d'un modérateur le peuvent.
- LE SYSTÈME DOIT conserver exactement les montants pour toute combinaison de mises (test par propriété).

Menaces : T-T10, T-T08. Test : `tests/abuse/test_as11_double_settlement.py`.

---

## US-12 Arbitrer un litige

> En tant que modérateur, je veux trancher les paris contestés en lisant les motifs pour que le résultat soit juste.

- US-12.1 QUAND un modérateur consulte la file des litiges, LE SYSTÈME DOIT lister les paris `disputed` avec le résultat proposé, les motifs et les totaux par issue, en n'affichant que des pseudonymes.
- US-12.2 QUAND un modérateur rend une décision `OUI`, `NON` ou `ANNULE` avec une justification de 20 à 1 000 caractères, LE SYSTÈME DOIT régler ou rembourser le pari et inscrire la décision au journal d'audit chaîné.
- US-12.3 LE SYSTÈME DOIT créer les comptes modérateurs uniquement par la commande `toutbet-admin create-moderator`, le mot de passe étant lu sur l'entrée standard.
- US-12.4 SI un modérateur tente de miser, de créer un pari ou d'accepter une invitation, ALORS LE SYSTÈME DOIT refuser (403).

### AS-12 Se promouvoir modérateur

> En tant que parieur malveillant, je veux m'attribuer le rôle modérateur (champ `role` à l'inscription, jeton modifié, appel direct à `/api/moderation`) pour trancher mes propres litiges.

- SI un corps de requête contient `role`, `kyc_status` ou tout champ non prévu, ALORS LE SYSTÈME DOIT refuser (422).
- SI un utilisateur dont le rôle en base n'est pas `moderator` appelle une route de modération, ALORS LE SYSTÈME DOIT refuser (403).
- LE SYSTÈME DOIT lire le rôle en base à chaque requête : le jeton ne porte aucun rôle.
- SI une route `/api` ne figure pas dans la liste des routes publiques, ALORS LE SYSTÈME DOIT exiger un jeton valide (401 sinon).

Menaces : T-E01, T-E02, T-S02, T-T04. Test : `tests/abuse/test_as12_privilege_escalation.py`.

---

## Abuse stories transverses

### AS-13 Saturer la plateforme pendant un grand match

> En tant que concurrent malveillant disposant d'un botnet, je veux inonder l'API de créations de paris et de mises pendant la finale de la Coupe du monde pour rendre le service indisponible.

- SI un utilisateur dépasse 20 requêtes d'écriture par minute, ALORS LE SYSTÈME DOIT répondre 429 avec `Retry-After`.
- SI un corps de requête dépasse 64 Ko, ALORS LE SYSTÈME DOIT répondre 413 sans le lire en entier.
- LE SYSTÈME DOIT limiter toute liste à 50 éléments par page.
- QUAND 500 utilisateurs simulés misent sur un même pari (Locust, atelier 2), LE SYSTÈME DOIT tenir le SLO de `STEERING.md` sans erreur 5xx.

Menaces : T-D01, T-D02, T-D03. Test : `tests/abuse/test_as13_event_flood.py` (puis Locust).

### AS-14 Collusion entre bookie, parieurs et modérateur

> En tant que bookie complice d'un parieur et d'un modérateur, je veux miser sur mon propre pari et faire valider un faux résultat contesté.

- SI le bookie tente de miser sur son pari ou d'accepter une invitation à son pari, ALORS LE SYSTÈME DOIT refuser (403 `BOOKIE_CANNOT_STAKE`).
- SI une décision de modération n'a pas de justification d'au moins 20 caractères, ALORS LE SYSTÈME DOIT la refuser (422).
- QUAND un modérateur tranche, LE SYSTÈME DOIT inscrire au journal d'audit chaîné le modérateur, la décision et la justification, et les rendre visibles aux participants.

Menaces : T-E04, T-R03, T-S06. Test : `tests/abuse/test_as14_collusion.py`.

### AS-15 Nier avoir parié

> En tant que perdant de mauvaise foi, je veux nier avoir effectué le pari pour récupérer ma mise.

- QUAND une mise est enregistrée, LE SYSTÈME DOIT chaîner au grand livre une écriture contenant l'utilisateur, la session, l'horodatage, la clé d'idempotence et l'identifiant de requête.
- LE SYSTÈME DOIT fournir un reçu signé vérifiable avec la clé publique exposée par `/api/receipts/public-key`.
- SI une écriture passée est modifiée en base, ALORS `toutbet-admin verify-ledger` DOIT échouer en indiquant la première écriture invalide.

Menaces : T-R01, T-R04, T-T08. Test : `tests/abuse/test_as15_repudiation.py`.

### AS-16 Faire parler l'application

> En tant qu'attaquant, je veux provoquer des erreurs (JSON invalide, types inattendus, injection SQL, chemins `../`) pour obtenir des stacktraces, des versions, des fichiers ou des données personnelles.

- SI une erreur non gérée survient, ALORS LE SYSTÈME DOIT renvoyer 500 avec un corps générique et un `request_id`, sans stacktrace ni nom de module.
- SI la validation échoue, ALORS LE SYSTÈME DOIT renvoyer les champs en erreur sans recopier les valeurs envoyées.
- SI un paramètre de filtre contient une injection SQL, ALORS LE SYSTÈME DOIT le refuser (422) par validation de liste blanche.
- SI un chemin de fichier statique contient `../` ou un encodage équivalent, ALORS LE SYSTÈME DOIT renvoyer 404.
- LE SYSTÈME DOIT désactiver `/docs`, `/redoc` et `/openapi.json` hors de l'environnement `dev`.
- LE SYSTÈME DOIT ne jamais écrire dans les logs un mot de passe, un jeton, un secret ou un numéro de carte.

Menaces : T-I02, T-I03, T-I08, T-T07. Test : `tests/abuse/test_as16_information_leak.py`.
