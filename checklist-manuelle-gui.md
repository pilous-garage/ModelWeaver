# Checklist de vérification MANUELLE — GUI ModelWeaver V2

À faire avec la vraie souris/clavier sur l'écran. Cocher au fur et à mesure.
Si un point échoue : noter l'étape, le comportement observé, et si possible un screenshot.

---

## 0. Préalable
- [ ] La GUI s'ouvre (2 fenêtres : `main` + `ide`)
- [ ] Le menu est en **français** (ou la langue choisie) et cohérent
- [ ] La barre de menu : Fichier / Fenêtre / Affichage / Langue / Panneaux

## 1. Menu — survol & ouverture
- [ ] Cliquer sur « Fichier » ouvre son sous-menu (fond accent sur l'item)
- [ ] Survoler « Fenêtre » pendant que Fichier est ouvert → **navigue** (Fichier se ferme, Fenêtre s'ouvre)
- [ ] Survoler un item d'un sous-menu → il se surligne (fond clair)
- [ ] **Quitter l'item survolé** (sans cliquer) → la surbrillance **disparaît**
- [ ] Passer la souris sur plusieurs items d'un même sous-menu → aucun ne reste surligné après
- [ ] Cliquer sur un **item d'action** (ex. un panel du catalogue) → le menu **se ferme** et l'action se lance
- [ ] Cliquer sur la **barre vide** entre les menus → le menu ouvert se ferme
- [ ] Cliquer **hors de la barre** (dans un panel) → le menu se ferme
- [ ] **Échap** → le menu se ferme
- [ ] Flèches **← →** quand un menu est ouvert → navigue entre les menus racines

## 2. Menu — actions clés
- [ ] Fenêtre → « Enregistrer » → persiste (pas d'erreur)
- [ ] Fenêtre → « Plein écran » (F11) → bascule plein écran / retour
- [ ] Fichier → « Quitter » → ferme la fenêtre courante (pas toute l'app)
- [ ] Affichage → Thèmes → Sombre / Clair → le fond change immédiatement
- [ ] Langue → English → le menu passe en anglais (réactif)
- [ ] Langue → Français → retour

## 3. Onglets — clic & drag
- [ ] **Clic simple** sur un onglet inactif → il s'active (jamais de split)
- [ ] **Drag** un onglet dans SA barre → reorder à la position du curseur (marqueur vertical)
- [ ] Pendant le drag : un **ghost** (clone flottant) suit la souris, l'onglet source est estompé
- [ ] **Drag au bord droit/gauche** d'un groupe → aperçu du split (overlay) → au relâcher, split horizontal
- [ ] **Drag au bord haut/bas** → aperçu → split vertical
- [ ] **Drag au centre du corps** d'un groupe → overlay « + Onglet en fin de file » → l'onglet s'ajoute à la fin

## 4. Cross-group (d'un groupe à l'autre)
- [ ] Drag un onglet du groupe **gauche** vers la **barre** du groupe droit → s'insère à la position visée
- [ ] Drag un onglet vers le **centre** du corps d'un autre groupe → s'ajoute en fin de file de CE groupe
- [ ] Drag un onglet vers le **bord** d'un autre groupe → le split se fait autour du groupe ciblé
- [ ] Les marqueurs (insertion/append/split) s'affichent sur le groupe **ciblé** (pas seulement la source)

## 5. Mini-layouts (ex slip views)
- [ ] Panneaux → Onglet → « Mini-layout » → un mini-layout apparaît (titre + sous-arbre)
- [ ] Le mini-layout a un **sous-arbre** (splits/groupes) à l'intérieur
- [ ] **Drag un onglet du groupe principal DANS le mini-layout** → s'y insère
- [ ] Le mini-layout a un **titre** et un bouton de fermeture ✕
- [ ] Fermer le mini-layout → il disparaît (et son sous-arbre)

## 6. Fenêtres (multi-fenêtres)
- [ ] Fenêtre → « Fenêtres ouvertes » → liste les fenêtres, la courante marquée (•)
- [ ] Cliquer sur une autre fenêtre de la liste → elle passe au **premier plan**
- [ ] Fenêtre → « Ouvrir une fenêtre » → templates (Dashboard, Par défaut, Agent IDE, Fenêtre vierge)
- [ ] Fenêtre → « Ouvrir une fenêtre » → **fenêtres préenregistrées** (Administration, Monitoring, Développement) → elles s'ouvrent AVEC leur layout + thème
- [ ] Redimensionner une fenêtre (drag bord) → position/taille persistées (~/.modelweaver/windows/<id>.json)
- [ ] Fermer une fenêtre → elle disparaît de la liste des autres fenêtres

## 7. Panels (rendus)
- [ ] Ressources : CPU/RAM/disque avec barres
- [ ] Agents : liste des agents (bug-busters, etc.) avec statut, boutons Redémarrer/Arrêter
- [ ] Ressources Docker : caches listés (mw-cache/…)
- [ ] Services : services supervisés avec PID + boutons ⟳/■
- [ ] Dépendances : listes requis/recommandé + bouton Installer
- [ ] Clés API : clés listées, verrou/suppression
- [ ] LLM locaux : moteurs détectés
- [ ] Catalogue modèles : modèles par provider (si synchro faite)
- [ ] Chat : envoyer un message → réponse
- [ ] Processus / Projets / LLM distant / Monitoring agents : données cohérentes
- [ ] Aucun badge d'erreur rouge (« Erreur du panel ») ni crash d'onglet

## 8. Thèmes & langue
- [ ] Les 4 thèmes (Sombre, Clair, OLED, Sépia) s'appliquent et restent cohérents
- [ ] Le thème d'une fenêtre préenregistrée est appliqué au boot (ex. Monitoring → OLED)
- [ ] Changer de langue puis **relancer la GUI** → elle démarre dans la langue choisie

## 9. Persistance (relance)
- [ ] Modifier le layout (drag/split/ajout) → recharger la GUI → le layout est **restauré**
- [ ] Vérifier `~/.modelweaver/layouts/layout-<fenêtre>.json` existe et est valide (YAML)
- [ ] Un séparateur redimensionné → la proportion est restaurée au reload

## 10. Divers / qualité
- [ ] Pas de lag quand on ouvre/ferme les menus
- [ ] Le drag ne sélectionne pas le texte des onglets
- [ ] Les boutons ✕ de fermeture d'onglet fonctionnent (stopPropagation, pas de drag)
- [ ] 3 fenêtres ouvertes simultanément → tout reste réactif
