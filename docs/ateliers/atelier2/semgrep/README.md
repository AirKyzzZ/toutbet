# Semgrep, scan avant correction

Scan du code tel que généré par P1 (commit `d83ec21`), lancé le 2026-11-25.

| Scan | Commande | Règles | Fichiers | Alertes |
|---|---|---|---|---|
| Plateforme (code + supply chain) | `semgrep ci` | 145 981 | 106 | 0 |
| Packs publics | `semgrep scan --config p/default --config p/owasp-top-ten --config p/python --config p/secrets --config p/docker-compose --config p/dockerfile --config p/javascript src tests deploy Dockerfile docker-compose.yml` | 1 724 | 94 | 0 |

`avant.json` est la sortie de `semgrep ci`, `avant-packs.json` celle des packs. Les 22 erreurs du JSON des packs sont des règles JavaScript qui demandent le moteur Pro, pas des alertes.

Résultats sur la plateforme : https://semgrep.dev/orgs/mxmnst-personal-org/findings?repo=AirKyzzZ/toutbet&ref=main
