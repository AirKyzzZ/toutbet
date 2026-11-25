# Étape 2 : dépendances, licences et SBOM

Scan du 2026-11-25 avec `semgrep ci --supply-chain` sur `main`. Les résultats sont envoyés sur Semgrep Cloud, onglet Supply Chain du dépôt `AirKyzzZ/toutbet`. Sortie de la commande : [`sca/semgrep-ci-supply-chain.txt`](sca/semgrep-ci-supply-chain.txt).

## Vulnérabilités

| Source | Dépendances | Règles | Résultat |
|---|---|---|---|
| `uv.lock` (PyPI) | 80, dont 34 en production | 19 138 : 12 097 paquets malveillants, 4 909 basiques, 2 132 avec atteignabilité | 0 vulnérabilité, 0 paquet malveillant |

Rien à prioriser aujourd'hui. Tout est aussi à la dernière version publiée : `uv tree --outdated --all-groups` ne signale aucun paquet en retard. C'était attendu : le projet a un jour, et le prompt P1 imposait les dernières versions stables.

La méthode quand une alerte arrivera, dans cet ordre :

1. **Atteignabilité.** Semgrep dit si notre code appelle la fonction vulnérable. Une CVE atteignable passe avant une CVE critique jamais appelée.
2. **EPSS.** Probabilité d'exploitation dans les 30 jours (FIRST). Au-dessus de 10 %, on corrige dans la journée. Le cours le dit : « EPSS ≠ tout, tout de suite ».
3. **Exploitée ou non.** Une CVE présente dans le catalogue KEV de la CISA, ou avec un exploit public, passe en tête quel que soit son score.
4. **Production ou outillage.** Les 46 paquets des groupes `dev`, `e2e` et `load` ne sont pas dans l'image Docker (`uv sync --no-dev`), donc ils passent après.

Dependabot (atelier 3, étape 1) ouvre la PR de correction. La CI la valide avant la fusion.

## Licences

Liste complète des 78 paquets installés, tous groupes compris et `toutbet` lui-même : [`sca/licences.csv`](sca/licences.csv), produite avec `pip-licenses`.

| Licence | Paquets | Ce qu'elle impose |
|---|---|---|
| MIT, MIT-0 | 40 | garder la mention de copyright |
| BSD (2 et 3 clauses) | 17 | idem |
| Apache-2.0, et doubles licences Apache/BSD | 9 | mention et fichier NOTICE |
| PSF-2.0, ZPL-2.1 | 4 | permissives |
| MPL-2.0 | 4 (`certifi`, `bidict`, `hypothesis`, `pathspec`) | copyleft par fichier : seulement si on modifie leurs fichiers |
| **LGPL-3.0** | 2 (`psycopg`, `psycopg-binary`) | copyleft faible, voir plus bas |
| **GPL / Artistic** | 1 (`text-unidecode`) | double licence, voir plus bas |

**Aucune licence n'oblige à ouvrir le code de ToutBet'.**

- `psycopg` est en LGPL-3.0, et c'est une dépendance de production. On l'importe sans le modifier, donc la LGPL ne s'étend pas à notre code. Il faut garder sa licence dans l'image et laisser possible son remplacement par une autre version : c'est le cas, c'est un paquet Python séparé.
- `text-unidecode` est sous GPL ou Artistic License, au choix. Il arrive par `pytest-playwright`, puis `python-slugify`, dans le groupe `e2e` uniquement. Il n'est ni distribué ni dans l'image. Même s'il l'était, on pourrait choisir l'Artistic License.
- Le dépôt lui-même n'a pas de licence (`toutbet` apparaît en `UNKNOWN`). Sans fichier `LICENSE`, un dépôt public reste « tous droits réservés ». Ça nous va pour un projet de cours. On le note pour décider plus tard.

Garde-fou proposé, pas mis en place : faire échouer la CI si une dépendance de production arrive sous GPL ou AGPL. Avec `pip-licenses --fail-on`, ça tient en une ligne. Semgrep peut aussi le faire avec une politique de licences.

## SBOM

À exporter depuis Semgrep : onglet Supply Chain, Dependencies, bouton Export SBOM. Il sera en annexe du rapport PDF de l'atelier.
