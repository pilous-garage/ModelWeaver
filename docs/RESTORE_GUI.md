# Restauration de la GUI du Projet ModelWeaver

Ce document décrit les étapes pour restaurer la **GUI du projet ModelWeaver** depuis l'historique Git.

---

## 1. Contexte
La GUI du projet ModelWeaver (développée avec **Tauri + React**) a été perdue suite à une suppression accidentelle. Elle a été restaurée depuis l'historique Git grâce au commit `a4756db` (`V0.5: GUI Installateur (Tauri) + tool_classes table`).

---

## 2. Étapes de Restauration

### 2.1. Identification du Commit Valide
Le dernier commit contenant la GUI était `a4756db`. Pour l'identifier :

```bash
cd /home/pierreloup2/PilousGarage/ModelWeaver
git log --oneline --name-only -- gui/installer/package.json | head -5
```

### 2.2. Restauration des Fichiers
Les fichiers de la GUI ont été restaurés depuis le commit `a4756db` :

```bash
git checkout a4756db -- gui/installer/
```

### 2.3. Vérification de la Restauration
Pour vérifier que les fichiers ont été restaurés :

```bash
find /home/pierreloup2/PilousGarage/ModelWeaver/gui/installer -type f | head -20
```

---

## 3. Installation des Dépendances

### 3.1. Installation des Dépendances Node.js
```bash
cd /home/pierreloup2/PilousGarage/ModelWeaver/gui/installer
npm install
```

### 3.2. Build du Projet Tauri
```bash
npm run tauri build
```

### 3.3. Lancement de la GUI en Mode Développement
```bash
npm run tauri dev
```

---

## 4. Structure de la GUI

### 4.1. Fichiers Clés
| Fichier | Description |
|---------|-------------|
| `package.json` | Dépendances et scripts du projet. |
| `src/App.tsx` | Composant principal React. |
| `src-tauri/src/main.rs` | Backend Tauri (Rust). |
| `src-tauri/tauri.conf.json` | Configuration Tauri. |

### 4.2. Dossiers Importants
| Dossier | Description |
|---------|-------------|
| `src/` | Code source React (frontend). |
| `src-tauri/` | Code source Tauri (backend Rust). |
| `public/` | Assets statiques (favicon, icons). |

---

## 5. Problèmes Courants

### 5.1. Erreur `npm install`
- **Cause** : Dépendances manquantes ou incompatibles.
- **Solution** : Supprimer `node_modules/` et `package-lock.json`, puis réinstaller.

### 5.2. Erreur `tauri build`
- **Cause** : Configuration Tauri incorrecte.
- **Solution** : Vérifier `tauri.conf.json` et les dépendances Rust.

### 5.3. Erreur `git checkout`
- **Cause** : Chemin incorrect ou fichier non versionné.
- **Solution** : Vérifier le chemin exact avec `git ls-tree -r <commit> --name-only`.

---

## 6. Sauvegarde
Pour éviter une nouvelle perte, sauvegardez régulièrement la GUI :

```bash
git add gui/installer/
git commit -m "Sauvegarde de la GUI"
git push
```

---

## 7. Conclusion
La GUI a été restaurée avec succès depuis l'historique Git. Pour la lancer :

```bash
cd /home/pierreloup2/PilousGarage/ModelWeaver/gui/installer
npm run tauri dev
```