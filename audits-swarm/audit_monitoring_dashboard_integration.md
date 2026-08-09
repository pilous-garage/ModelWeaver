# Audit des panneaux de monitoring et intégration de la télémétrie dans le dashboard

**Projet :** mw-swarm  
**Branche :** `auto_code_412`  
**Date :** généré par l'agent de relecture

---

## Contexte

Ce rapport synthétise l'audit des panneaux de monitoring du dashboard ModelWeaver
et formule les recommandations pour intégrer la télémétrie collectée (latence,
taux d'erreur, disponibilité des providers, métriques système) dans les
panneaux existants.

---

## 1. Problèmes détectés lors de la relecture

### 1.1 Problème unitaire : `panel-creator` hors service

- **Service :** `panel-creator` (pid 320072)
- **Statut :** `alive: false` — le service ne répond plus.
- **Impact :** la génération/découverte des panneaux GUI peut être bloquée.
- **Recommandation :** redémarrer le service et surveiller la reprise du socket.

### 1.2 Problèmes structurels identifiés

- **Rapports tronqués :** `rapport.txt` et `recommandations.txt` sont incomplets
  (contenu coupé). Ils doivent être consolidés dans un document unique.
- **`report_problems.txt`** reste un simple pointeur sans détail exploitable.
- **Traçabilité partielle :** les fichiers de suivi à la racine ne sont pas
  reliés entre eux (pas de lien vers le rapport final).

---

## 2. État des processus système

| Processus | PID | Socket | Statut |
|-----------|-----|--------|--------|
| agent-manager | 1135817 | — | ✅ vivant |
| api | 1180748 | 8770 | ✅ vivant |
| catalogue | 320068 | 8765 | ✅ vivant |
| installer | 320069 | — | ✅ vivant |
| llm-manager | 320070 | — | ✅ vivant |
| model-sync | 320071 | — | ✅ vivant |
| **panel-creator** | 320072 | — | ❌ **DOWN** |
| tester | 320073 | — | ✅ vivant |
| watcher | 320075 | — | ✅ vivant |
| usage-batcher | 676566 | — | ✅ vivant |

**Total :** 10 process supervisés — **9 vivants, 1 en panne** (`panel-creator`).

---

## 3. Recommandations d'intégration de la télémétrie dans le dashboard

### 3.1 Sources de télémétrie disponibles
- `/v1/monitoring/metrics` — métriques exposées par le monitoring.
- Module `telemetry` — collecte latence, taux d'erreur, disponibilité provider.
- Collecteur de métriques système (CPU, RAM, processus).

### 3.2 Panneaux cibles
1. **SystemDashboardPanel** — intégrer CPU/RAM/disque et état des services.
2. **ServicesMonitorPanel** — afficher le statut live des services (dont `panel-creator`).
3. **Panneau télémétrie providers** — latence / taux d'erreur / uptime par provider.

### 3.3 Actions recommandées
- Redémarrer `panel-creator` et vérifier le socket.
- Consolider les rapports tronqués en un document unique et complet.
- Brancher les panneaux sur `/v1/monitoring/metrics` pour un rafraîchissement temps réel.
- Ajouter des seuils d'alerte (ex. uptime < 99 %, latence > seuil).

---

## 4. Conclusion

Le dépôt `mw-swarm` est sain et propre sur `auto_code_412`. Le principal point
d'attention opérationnel est le service `panel-creator` hors service. Les
recommandations d'intégration télémétrie sont formulées ci-dessus et doivent
être implémentées dans les panneaux de monitoring du dashboard.

*Rapport d'audit généré par l'agent de relecture.*