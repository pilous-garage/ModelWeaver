# ModelWeaver GUI v2 — Spécification technique

> Repartie de zéro dans `interfaces/main/GUI/v2/` (l'ancienne GUI `official/gui`
> reste intacte comme référence). L'architecture corrige les défauts de la v1 :
> 3 systèmes de layout superposés, splits qui crashent, panels conteneurs 100vh,
> menus dupliqués, état React orphelin.

---

## 1. Principes fondateurs

1. **Le layout est la source de vérité.** Toute modification d'une fenêtre
   (ouvrir/fermer/déplacer/splitter un panel, activer un onglet) passe par la
   **mutation du layout**, persisté **en temps réel** (fichier `.layout.yaml`).
   Aucun état React parallèle qui puisse diverger.
2. **Une seule barre de menu globale**, régénérée après **chaque résolution de
   layout**. Pour les slip views, le menu du sous-arbre n'apparaît **que si
   la slip view est affichée** (active).
3. **Multilingue natif dès maintenant.** Aucune chaîne écrite en dur : chaque
   texte est une **clé** résolue via un fichier `.lang.<locale>.yaml` (un global
   + un par panel).
4. **Sessions.** L'app ouvre une session (ensemble de fenêtres + layouts,
   fichier `.session.yaml`). On peut **switcher** de session.
5. **Tout est textuel et pilotable.** Le backend (agents/tests) peut inspecter
   la GUI (arbre DOM + coordonnées) et simuler des actions, sans screenshot.

---

## 2. Arborescence du dossier v2

```
interfaces/main/GUI/v2/
├── package.json
├── vite.config.ts
├── index.html
├── src/
│   ├── main.tsx               # boot : vérifie le superviseur, charge la session
│   ├── App.tsx                # rendu de la session (fenêtres Tauri)
│   ├── boot.ts                # vérification superviseur/daemon + démarrage
│   ├── session.ts             # chargement/sauvegarde/switch des sessions
│   ├── layout/
│   │   ├── types.ts           # modèles Layout, PanelNode, Group, Panel
│   │   ├── resolve.ts         # résolution arbre → structure de rendu + menu
│   │   ├── ops.ts             # opérations de mutation (add/close/move/split)
│   │   └── persist.ts         # sauvegarde temps réel (.layout.yaml)
│   ├── menu.ts                # construction du menu global (réactif au layout)
│   ├── i18n.ts                # résolution de clés (.lang.*.yaml)
│   ├── theme.ts               # injection du CSS du thème (<style id="mw-theme">) + carte mw-*
│   ├── bridge.ts              # IPC Tauri + daemon HTTP
│   ├── inspector.ts           # traduction DOM + simulation d'actions
│   ├── components/
│   │   ├── MenuBar.tsx
│   │   ├── SplitTree.tsx      # splits redimensionnables
│   │   ├── TabGroup.tsx       # groupe d'onglets (drag/drop + split)
│   │   └── SlipView.tsx     # panel-fenêtre avec sous-arbre
│   └── panels/
│       ├── ressources/        # panel de base #1
│       ├── etat-systeme/      # panel de base #2
│       └── _registry.ts       # déclaration des panels (id, label, params, lang)
├── layouts/                   # .layout.yaml par défaut (source de vérité)
│   ├── default.layout.yaml
│   └── vide.layout.yaml
├── sessions/                  # .session.yaml
│   └── principale.session.yaml
└── lang/
    ├── global.lang.fr.yaml    # clés globales (menus, boutons communs)
    ├── global.lang.en.yaml
    ├── ressources.lang.fr.yaml
    └── etat-systeme.lang.fr.yaml
```

---

## 3. Modèle de données

### 3.1 Layout (`.layout.yaml`) — une fenêtre

```yaml
id: default
label: "Fenêtre principale"
theme: dark
menu_extra: []            # items de menu supplémentaires spécifiques au layout
tree:
  direction: horizontal
  sizes: [50, 50]
  children:
    - type: group
      id: pg-gauche
      tabs:
        - { panel: ressources, params: { vue: compact } }
        - { panel: etat-systeme }
      active: ressources
    - type: group
      id: pg-droit
      tabs:
        - { panel: etat-systeme }
      active: etat-systeme
```

**Nœuds possibles :**

| type | rôle |
|---|---|
| `group` | espace à onglets. `tabs[]` = liste d'occurrences de panels, `active` = onglet courant |
| `slip` (slip view) | panel-fenêtre avec son **propre `tree`** (sous-arbre imbriqué) + `title` + ses propres `menu_extra` |
| split (nœud racine/imbriqué) | `direction: horizontal\|vertical`, `sizes[]`, `children[]` |

### 3.2 Règle fondamentale : un panel vit TOUJOURS dans un onglet

> **Un panel n'occupe JAMAIS directement l'espace d'un conteneur.** Il est
> toujours porté par un onglet (`group.tabs[]`). Un `group` a au moins 1 onglet ;
> un `slip` (slip view) a au moins 1 onglet dans son sous-arbre.

Raisons :
- **Sécurité du layout** : chaque panel a une **identité d'occurrence** (`occId`)
  et un cycle de vie (actif/inactif) clairs ; on peut le fermer, le réordonner,
  le déplacer entre groupes, sans risque d'orchestre incohérent.
- **Uniformité** : le rendu ne gère que 2 cas de feuille — `group` (onglets)
  et `slip` (slip view avec sous-arbre). Pas de cas "panel nu".
- **Évolution** : plus tard, si on veut qu'un panel **remplisse tout l'espace**
  d'un conteneur (sans barre d'onglets visible), on ajoutera un mode
  `group.hideTabs: true` qui masque la barre tout en gardant le conteneur onglet
  (l'onglet unique est invisible mais le panel reste dans un groupe). Pour le
  MVP, la barre d'onglets est toujours visible.

**Invariants** (vérifiés par `resolve()` et par les tests) :
- Chaque `group` a `tabs.length >= 1` et `active` référence un `tabs[]` existant.
- Chaque occurrence de panel a un `occId` unique dans l'arbre.
- Chaque `slip` a un `tree` non vide (au moins 1 onglet quelque part).

### 3.3 Occurrence de panel (dans `tabs[]`)

```yaml
- panel: ressources          # id du panel (registry)
  params: { vue: compact }   # arguments passés au composant (facultatif)
  occId: occ-1               # id d'occurrence unique (auto-généré si absent)
  theme: { panel: sombre }   # thème panel surchargé pour CETTE occurrence (facultatif)
```

Le composant du panel reçoit `params` en prop. Deux onglets peuvent référencer
le même panel avec des params différents (chacun a son `occId`).

### 3.3 Session (`.session.yaml`) — ensemble de fenêtres

```yaml
id: principale
label: "Session principale"
windows:
  - id: main
    title: "Principale"
    layout: default          # référence un .layout.yaml
    theme: dark
    x: 0
    y: 0
    width: 1400
    height: 900
    maximized: false
  - id: ide
    title: "IDE"
    layout: ide
    x: 0
    y: 0
    width: 1200
    height: 800
```

Le switch de session : fermer les fenêtres de la session courante, charger le
fichier `.session.yaml` cible, ouvrir ses fenêtres.

---

## 4. Résolution de layout → rendu + menu

`resolve(layout) → { structure, menu, slipViews }`

Après **chaque** changement de layout (add/close/move/split/activate) :

1. On re-résout l'arbre.
2. On régénère le **menu global** à partir de :
   - `menu_extra` du layout courant,
   - les panels présents dans l'arbre (leurs items de menu déclarés),
   - la **slip view active** : si un `slip` est affiché, ses `menu_extra`
     + les menus de ses panels sont injectés ; sinon, masqués.
3. On persiste le layout (temps réel).

Exemple de groupe de slip views : un panneau contient 3 slip views
(IDE, Chat dev, Ressources). Le menu du bandeau change selon la slip view
active → permet "une fenêtre avec multiples IDE, chat de dev, et un bandeau
à droite avec le gestionnaire de ressources".

---

## 5. Opérations de mutation (layout/ops.ts)

Toutes mutent le layout puis appellent `persist()` + `resolve()` :

- `addPanel(layout, groupId, panelId, params?)` — ajoute un onglet
- `closeTab(layout, groupId, occId)` — ferme un onglet (supprime le groupe si vide)
- `activateTab(layout, groupId, occId)` — change l'onglet actif
- `moveTab(layout, fromGroup, toGroup, occId, index?)` — déplace un onglet
- `splitGroup(layout, groupId, direction, occId, fromGroup?)` — split en 2 zones
- `resizeSplit(layout, nodeId, sizes)` — ajuste les tailles
- `extractTabToWindow(layout, groupId, occId, session)` — sort un onglet en vraie fenêtre
- `addSlipView(layout, panelId, params?)` / `closeSlipView` / `activateSlipView`

Chaque opération est **purement fonctionnelle** (retourne un nouveau layout),
testable sans UI.

**Règle d'or des opérations** : aucune opération ne pose un panel "nu".
`addPanel` crée (ou réutilise) un `group` et pousse l'onglet dedans.
`addSlipView` crée un `slip` avec un sous-arbre à 1 onglet. La fermeture
d'un onglet ne vide jamais un groupe (un groupe vide est supprimé de l'arbre).

---

## 5bis. Drag & drop des onglets + manipulation des fenêtres

### 5bis.1 Buts

1. **Réordonner** les onglets d'une même barre (drag horizontal).
2. **Déplacer** un onglet vers un autre groupe (d'une autre fenêtre ou d'une
   slip view) — survol de la barre cible → s'y ajoute.
3. **Prévisualiser** une slip view au survol de son onglet (layout temporaire).
4. **Extraire** un onglet en **vraie fenêtre** Tauri (drag en dehors de toute
   fenêtre, comme un navigateur).
5. **Zone de drop intelligente** : selon où on relâche → réordonner, ajouter au
   groupe, ou **split**.
6. **Ajuster** les tailles (splits redimensionnables + fenêtre Tauri).
7. **Scroll** des panels plus grands que leur contenu (2 sens).
8. **Annuler** un drag (clic droit ou Echap).

### 5bis.2 Zones de drop (où lâcher un onglet)

| zone cible | comportement |
|---|---|
| sur un **onglet** d'une barre | réordonne (même groupe) ou insère à cette position (autre groupe) |
| sur la **barre d'onglets** (zone vide) | ajoute à la fin du groupe cible |
| sur un **bord** du groupe (top/bottom/left/right) | **split** en 2 zones (le panel part dans la nouvelle zone) |
| au **centre** du groupe | ajoute au groupe (onglet actif) |
| **en dehors** de toute fenêtre (bureau) | **extrait** : crée une nouvelle fenêtre Tauri avec ce panel/slip view |
| sur un **onglet de slip view** | prévisualise son layout (sans changer l'actif) ; le drop y ajoute/split dedans |

### 5bis.3 Prévisualisation d'une slip view (survol)

- Mouse over sur un **onglet de slip view** → affiche une **preview flottante**
  de son layout (sans activer l'onglet). On voit où on déposera avant de relâcher.
- Le survol prolongé (hover) active temporairement la slip view pour permettre
  un drop "dans" son sous-arbre.

### 5bis.4 Extraction d'un onglet en fenêtre

- Si le drop se termine **hors de toute fenêtre** : crée une nouvelle fenêtre
  Tauri (via `create_window`) dont le layout contient le panel/slip view extrait,
  et **retire** l'onglet de son groupe source (le groupe est supprimé s'il se vide).

### 5bis.5 Ajustement des tailles

- **Splits** : séparateurs redimensionnables (`resizeSplit` — drag le séparateur).
- **Fenêtre Tauri** : bords natifs (position/taille persistées dans la session).

### 5bis.6 Scroll

- Les panels plus grands que leur contenu défilent dans **les 2 sens**.
- **Molette** = scroll vertical. Scroll horizontal via **scrollbar** et
  **Shift+molette** (pas de scroll horizontal par défaut sur la molette).
- Classes : `mw-scroll` (overflow auto 2 sens), la molette garde le vertical.

### 5bis.7 Annulation d'un drag

- **Clic droit** pendant le drag → annule (restaure l'état, `dragend` annulé).
  Les pilotes souris (X11/Wayland) délivrent les boutons indépendamment : un
  clic droit pendant un bouton gauche maintenu est un événement séparé — c'est
  donc techniquement fiable.
- **Echap** → annule (standard universel).
- Implémentation (drag **custom** mousedown/mousemove/mouseup, choisi pour
  éviter le crash WebKitGTK du DragEvent natif) : on écoute `contextmenu` et
  `mousedown` (button=2) pendant le drag → on annule proprement. Un `dragend`
  est toujours émis (état annulé) pour que l'inspecteur/le layout restent
  cohérents.

### 5bis.8 Implémentation (inspector.ts + components)

- Le drag d'onglet est **custom** (mousedown → mousemove → mouseup), pas un
  `DragEvent` natif (évite le crash WebKitGTK headless rencontré en v1).
- Pendant le drag, on calcule la **zone de drop** au survol (les bords →
  split, la barre → ajouter, l'onglet → réordonner, le bureau → extraire).
- La prévisualisation de slip view et le drop dans un sous-arbre passent par
  les opérations de layout pures (`moveTab`, `splitGroup`, `addSlipView`,
  `extractTabToWindow`).

---

## 6. Menus (menu.ts)

Un **seul** `MenuBar` global en haut de la fenêtre. Construction :

```ts
function buildMenu(layout, activeSlipView): MenuItem[]
```

- Items globaux fixes : `Fichier` (nouvelle fenêtre, session, quitter),
  `Fenêtre` (listes des fenêtres ouvertes, ouvrir une fenêtre),
  `Affichage` (plein écran), `Langue` (switch de locale).
- Items du layout : `menu_extra` + menus des panels présents.
- Items de la slip view active (si `slip` affiché).

### 6.1 Structure `MenuItem`

```ts
interface MenuItem {
  id?: string;
  labelKey?: string;          // clé i18n (jamais de texte en dur)
  type?: "normal" | "separator" | "toggle" | "radio";
  checked?: boolean;          // pour toggle/radio
  action?: string;            // action à exécuter (onMenuAction)
  shortcut?: string;          // raccourci affiché (ex. "Ctrl+W")
  disabled?: boolean;
  items?: MenuItem[];         // sous-menu
}
```

Le `path` d'un item de panel (ex. `["Panneaux", "Ressources"]`) désigne le
chemin de sous-menus à créer/joindre dans le menu global : on fusionne les
items de même chemin (les menus des panels se regroupent sous "Panneaux →
Ressources").

### 6.2 Actions

Les actions sont des chaînes résolues par le gestionnaire d'actions de la
fenêtre (`onMenuAction`) :
- Actions système : `app:quit`, `window:fullscreen`, `window:close`,
  `session:switch:<id>`, `lang:set:<locale>`, `theme:set:<niveau>:<theme>`.
- Actions de layout : `panel:add:<panelId>`, `panel:close:<occId>`,
  `panel:activate:<occId>`, `slip:activate:<occId>`, `layout:save`,
  `tab:extract:<occId>`.
- Actions de panels : `panel:<id>:<action>` — dispatchées au panel actif
  (via son `onMenuAction`).


---

## 7. Panels (panels/_registry.ts)

```ts
interface PanelDef {
  id: string;
  labelKey: string;              // clé i18n (pas de texte en dur)
  version: string;
  paramsSchema?: Record<string, any>;  // déclaration des params acceptés
  defaultParams?: Record<string, any>;
  langFiles?: string[];          // .lang.<locale>.yaml spécifiques
  menu?: MenuItemDef[];          // items de menu du panel
  component: React.FC<{ ctx: PanelContext; params: Record<string, any> }>;
}
```

Le `PanelContext` expose : `api` (daemon), `layout`, `onMenuAction`,
`activateSlipView`, `addSlipView`, etc.

### Panels de base (v1 de la GUI v2)

1. **`ressources`** : CPU / RAM / disque. Route daemon : `system/state/get`
   (ou `ressource_manager`). Params : `{vue: compact|detail}`.
2. **`etat-systeme`** : état global (services, version, agents actifs).
   Route : `system/info` + `service/list`.

---

## 8. Sessions (session.ts)

- `loadSession(id)` : lit `sessions/<id>.session.yaml`, ouvre les fenêtres
  Tauri correspondantes.
- `saveSession(id)` : persiste positions/tailles/layouts actuels.
- `switchSession(id)` : ferme les fenêtres courantes, charge la nouvelle.
- La session active est mémorisée (`~/.modelweaver/session-active`).

---

## 9. Boot (boot.ts + main.tsx)

Au démarrage de l'app :

1. **Vérifier le superviseur** : interroger le daemon (`/health` ou socket).
   Si absent → tenter de le lancer (le binaire délègue au superviseur Python
   s'il existe, sinon démarre les services). Le superviseur vérifie le reste
   (daemon, catalogue, services).
2. Charger la session active (ou la session par défaut).
3. Ouvrir les fenêtres de la session.
4. Démarrer le poller d'inspection (inspector.ts).

---

## 10. Traducteur de fenêtre + simulation (inspector.ts)

Réutilise le design de la v1 (`gui/inspect`, `gui/act`, poller) **corrigé** :

- **Inspection** : arbre DOM textuel + coordonnées (`getBoundingClientRect`),
  retourné via le daemon (`gui/status`). Le backend voit chaque élément
  (onglet, bouton, panel) avec sa boîte (x/y/w/h).
- **Actions** : `click`, `hover`, `hover-out`, `mousemove`, `type`, `drag`.
  Le drag corrige le bug WebKitGTK de la v1 : **ne pas** dispatcher un
  `DragEvent` natif avec `DataTransfer` (crash headless). À la place, les
  opérations de drag appellent directement les opérations de layout
  (`moveTab`/`splitGroup`) via un canal dédié, ou ciblent les handlers React.
- **Ciblage par fenêtre** : chaque commande porte `window` (label) ; le poller
  de la fenêtre ciblée seule l'exécute.

Scripts d'exemple (utiles pour mgx/agents) :
- `scripts/gui-inspect.sh` : POST `gui/inspect` + attend `gui/status` → JSON.
- `scripts/gui-act.sh` : POST `gui/act` (click/hover/drag/type).

---

## 11. i18n (i18n.ts) — multilingue

- Chaque chaîne visible est une **clé** : `t('menu.fichier')`.
- Fichiers : `lang/global.lang.fr.yaml`, `panels/ressources/ressources.lang.fr.yaml`, etc.
- Structure d'un fichier lang :

```yaml
menu:
  fichier: "Fichier"
  fenetre: "Fenêtre"
  quitter: "Quitter"
panels:
  ressources:
    titre: "Ressources"
    cpu: "CPU"
```

- Le panel déclare ses `langFiles` ; au chargement, toutes les langues de tous
  les panels sont fusionnées en un dictionnaire.
- `Langue` dans le menu → switch locale (persistée `~/.modelweaver/locale`).

---

## 12. Gestion des thèmes

### 12.1 Approche : un thème = du vrai CSS

Chaque composant de la GUI expose des **classes sémantiques stables** (design
system `mw-*`). Le thème est un **fichier `.css` complet** qui cible ces
classes — on peut donc modifier **tous les éléments affichés** (onglet, panel,
menu, bouton, titre, tab bar, split separator…) : font, couleur du texte,
couleur du background, forme (border-radius, border, padding, shadow), etc.
Presque aussi complet que du CSS.

### 12.2 Deux composantes de thème

Un thème a **deux parties** (deux fichiers, ou deux sections dans un fichier) :

| composante | cible | exemples |
|---|---|---|
| **global** | l'interface commune (indépendante des panels) | menu, onglets, splits, boutons, inputs, scroll, slip-view |
| **panel** | le contenu des panels (les classes `mw-panel-*` + classes internes de chaque panel) | titres, tableaux, cartes, graphiques, badges des panels |

Un **thème générique** = un thème global réutilisable (dark, light, high-contrast…)
qui ne dépend pas du contenu des panels. Il définit les variables `:root` +
toutes les classes `mw-*` communes.

Exemple de thème global (`themes/dark.global.css`) :

```css
:root {
  --mw-bg: #0f172a; --mw-bg-panel: #1e293b; --mw-fg: #e2e8f0;
  --mw-accent: #0f3460; --mw-border: #334155;
  --mw-font-ui: 'Inter', system-ui, sans-serif; --mw-font-mono: 'JetBrains Mono', monospace;
}
.mw-menu-bar { background: var(--mw-bg); border-bottom: 1px solid var(--mw-border); }
.mw-menu-item:hover { background: var(--mw-accent); color: #fff; }
.mw-tab-bar { background: #0f172a; border-bottom: 1px solid var(--mw-border); }
.mw-tab { font-size: 12px; color: #94a3b8; }
.mw-tab-active { background: var(--mw-bg-panel); color: var(--mw-fg); font-weight: 600; }
.mw-split-separator { background: var(--mw-border); }
.mw-btn { background: #334155; color: var(--mw-fg); border: none; border-radius: 6px; padding: 4px 10px; }
```

Exemple de thème panel (`themes/compact.panel.css`) :

```css
.mw-panel { background: var(--mw-bg-panel); border: 1px solid var(--mw-border); border-radius: 6px; padding: 4px; }
.mw-panel-title { font-size: 11px; font-weight: 700; color: #94a3b8; }
.mw-panel-ressources .mw-gauge { height: 6px; }
.mw-panel-etat .mw-status-dot { width: 8px; height: 8px; }
```

### 12.3 Portée d'application (panel / fenêtre / session)

Le thème s'applique à **trois niveaux**, du plus large au plus précis
(le niveau précis surcharge le général) :

1. **Session** → `session.theme.global` : thème global appliqué à toutes ses fenêtres.
2. **Fenêtre** (layout) → `layout.theme.global` : surcharge le global de la session
   (ex. une fenêtre claire dans une session sombre). `layout.theme.panel` : thème
   panel par défaut pour les panels de cette fenêtre.
3. **Panel** (occurrence) → l'occurrence peut déclarer `theme.panel` : surcharge
   le thème panel de la fenêtre pour CE panel seulement (ex. un panel graphique
   sombre dans une fenêtre claire).

**Résolution en cascade** : `session.global` → `fenêtre.global` →
`fenêtre.panel` → `panel.panel`. Le CSS est concaténé dans cet ordre (le plus
précis en dernier → il gagne par spécificité).

```yaml
# session
theme: { global: dark }        # thème global générique

# layout (fenêtre)
theme: { global: light, panel: compact }

# occurrence de panel dans le layout
- panel: ressources
  theme: { panel: sombre-graphique }   # surcharge pour CE panel
```

### 12.4 Thèmes génériques + custom

- **Génériques** : `dark`, `light`, `high-contrast` (fichiers `.global.css`
  dans `interfaces/defaults/themes/`). Réutilisables partout.
- **Custom** : thèmes utilisateur dans `~/.modelweaver/themes/*.css` (global
  et/ou panel). Créés/édités via `theme/save`.
- Le menu `Affichage → Thème` propose : `Session`, `Fenêtre`, `Panel` (sous-menus
  pour choisir à quel niveau appliquer, puis le thème).

### 12.5 Classes sémantiques (`mw-*`) — carte des éléments

La GUI v2 utilise un **set fixe et documenté de classes** (une "carte des
éléments" par type de composant), que les thèmes ciblent. Chaque composant
pose sa classe **en plus** de ses styles inline minimaux (le CSS du thème
prime via `!important` ou par priorité de classe).

| composant | classes |
|---|---|
| barre de menu | `mw-menu-bar`, `mw-menu-item`, `mw-menu-item-active`, `mw-menu-separator` |
| barre d'onglets | `mw-tab-bar`, `mw-tab`, `mw-tab-active`, `mw-tab-close` |
| zone panel | `mw-panel`, `mw-panel-title`, `mw-panel-body` |
| boutons | `mw-btn`, `mw-btn-primary`, `mw-btn-danger` |
| splits | `mw-split-root`, `mw-split-separator` (direction hor/vert) |
| slip view | `mw-slip-view`, `mw-slip-view-title`, `mw-slip-view-body` |
| divers | `mw-input`, `mw-scroll`, `mw-badge`, `mw-tree`, `mw-status-dot` |

L'éditeur de thème peut lister "tous les éléments de chaque type" en
parcourant cette carte → l'utilisateur modifie la propriété voulue (font,
couleur, forme) pour chaque classe.

### 12.6 Interfaces React

- Les composants posent leurs classes : `className="mw-tab mw-tab-active"`.
- Les styles inline sont **minimaux** (position, flex, overflow) ; tout ce qui
  est esthétique (couleurs, fonts, bordures, radius) passe par le CSS.
- Un thème s'applique en injectant `<style id="mw-theme">{css}</style>` à la
  racine (un simple `String`, React n'a aucun souci à interfacer avec ça).

### 12.7 Fichiers + routes

- Thèmes : `interfaces/defaults/themes/*.{global,panel}.css` (défauts) +
  `~/.modelweaver/themes/*.{global,panel}.css` (utilisateur).
- Routes daemon : `theme/list` (avec type global/panel), `theme/get` (contenu CSS),
  `theme/save`.
- Par défaut, un CSS de base (`dark.global.css` + `default.panel.css`) définit
  les variables `:root` + le style de toutes les classes `mw-*` (le rendu est
  correct sans thème custom).

---

## 13. Ajout de panels + compilation à chaud

### 13.1 Deux sources de panels

| source | chargement | quand |
|---|---|---|
| **essentiel** (`essential: true`) | compilé dans le bundle GUI | au boot (panels de base : ressources, état-système, ...) |
| **externe** | compilé par `panel-creator` (esbuild), servi par le daemon | à la demande (chargement paresseux) |

### 13.2 Contrat d'un panel (`.panel.tsx`) — référence complète

```tsx
export const Panel: PanelDef = {
  // ── Identité ──
  id: "ressources",              // id unique (utilisé dans le layout)
  labelKey: "panels.ressources.titre", // clé i18n (jamais de texte en dur)
  iconKey: "panels.ressources.icone",  // clé i18n pour l'icône/label d'onglet (facultatif)
  version: "1.0.0",              // version semver (rechargement à chaud si change)
  essential: true,               // true = compilé dans le bundle GUI (au boot)
                                 // false/absent = externe (compilé par panel-creator)

  // ── Paramètres d'ouverture ──
  paramsSchema: {                // déclaration des params acceptés (validation)
    vue: { type: "string", enum: ["compact", "detail"], default: "detail" },
    autoRefresh: { type: "boolean", default: true },
  },
  defaultParams: { vue: "detail", autoRefresh: true },

  // ── i18n ──
  langFiles: ["ressources.lang.fr.yaml", "ressources.lang.en.yaml"],

  // ── Menu à insérer ──
  // Ces items sont injectés dans le menu global quand le panel est présent
  // dans le layout (et, pour une slip view, seulement si elle est active).
  menu: [
    { path: ["Panneaux", "Ressources"], id: "ressources:refresh",
      labelKey: "panels.ressources.menu.refresh", action: "panel:ressources:refresh" },
    { path: ["Panneaux", "Ressources"], type: "separator" },
    { path: ["Panneaux", "Ressources"], id: "ressources:config",
      labelKey: "panels.ressources.menu.config", action: "panel:ressources:config" },
  ],

  // ── Thème panel ──
  // Fichier CSS spécifique au contenu du panel (classes mw-panel-<id>-*).
  // S'applique à toutes les occurrences de ce panel.
  themeCss: "panels/ressources/ressources.panel.css",

  // ── Cycle de vie ──
  onActivate(ctx, params) { /* appelé quand l'onglet devient actif */ },
  onDeactivate(ctx, params) { /* appelé quand l'onglet devient inactif */ },
  onParamsChange(ctx, oldParams, newParams) { /* si params modifiés à chaud */ },

  // ── Description (inspecteur) ──
  declaration: () => `[ressources] Ressources v1.0.0\n  CPU/RAM/disque\n  Routes: system/state/get`,

  // ── Rendu ──
  component: ({ ctx, params }) => <RessourcesPanel ctx={ctx} params={params} />,
};
```

**`PanelDef` complet :**

| champ | type | requis | rôle |
|---|---|---|---|
| `id` | string | ✅ | id unique du panel |
| `labelKey` | string | ✅ | clé i18n du label |
| `iconKey` | string | | clé i18n de l'icône (onglet) |
| `version` | string | ✅ | semver, pour le rechargement à chaud |
| `essential` | bool | | true = bundle GUI, sinon externe |
| `paramsSchema` | object | | schéma des params (validation) |
| `defaultParams` | object | | params par défaut |
| `langFiles` | string[] | | fichiers lang du panel |
| `menu` | MenuItemDef[] | | items à insérer dans le menu global |
| `themeCss` | string | | CSS du contenu du panel (classes `mw-panel-<id>-*`) |
| `onActivate` | fn | | à l'activation de l'onglet |
| `onDeactivate` | fn | | à la désactivation |
| `onParamsChange` | fn | | si les params changent à chaud |
| `declaration` | fn→string | ✅ | description textuelle (inspecteur/LLM) |
| `component` | fc | ✅ | rendu `({ctx, params}) => ...` |

**`PanelContext` (ctx) :**

```ts
interface PanelContext {
  api: DaemonApi;                       // accès au daemon (HTTP)
  layout: Layout;                       // layout courant de la fenêtre
  params: Record<string, any>;          // params de l'occurrence
  onMenuAction(action: string): void;   // action de menu
  addTab(groupId, panelId, params?): void;
  closeTab(groupId, occId): void;
  activateTab(groupId, occId): void;
  activateSlipView(occId): void;
  closeSlipView(occId): void;
  extractTabToWindow(groupId, occId): void;  // sort un onglet en vraie fenêtre
  setPanelTheme(occId, theme): void;    // surcharge le thème panel d'une occurrence
  t(key: string): string;               // i18n
}
```

**Contrat de validation (panel-creator)** — un panel est valide si :
- expose `export const Panel: PanelDef` ;
- `id`, `labelKey`, `version`, `declaration`, `component` présents ;
- `menu[].labelKey` (pas de texte en dur), `menu[].action` présent pour les items ;
- les classes utilisées par le rendu commencent par `mw-panel-<id>-` (cohérence
  avec `themeCss`) ;
- les `paramsSchema` types reconnus (string/number/boolean/enum).


### 13.3 Registre + compilation

- Le registre des panels externes : `~/.modelweaver/panels/index.json`
  (produit par `panel-creator`, consommé par `panels/index`).
- `panel-creator` **valide le contrat** (export `Panel: PanelDef`, champs requis),
  **compile** en module ES autonome (esbuild), gère le **React partagé**
  (import ré-exporté depuis le daemon), et enregistre au registre.
- Routes daemon (réutilisées de la v1) :
  - `panels/index` — liste des panels externes
  - `panels/file/<id>` — sert le JS compilé (import dynamique)
  - `panels/build` — recompile les panels externes (force optionnel)
  - `panels/bundles/list|get|build` — groupements de panels

### 13.4 Compilation à chaud

- À l'ajout d'un panel dans le layout, si le panel n'est **pas encore compilé** :
  le frontend appelle `panels/build` → `panel-creator` compile via esbuild →
  le registre est mis à jour → le frontend recharge le panel (import dynamique).
- **esbuild** est disponible dans l'image Docker (`modelweaver-base`) et sur
  l'hôte (node_modules GUI) — la compilation fonctionne partout.
- Flux d'ajout d'un nouveau panel externe :
  1. Le développeur écrit `mon-panel.panel.tsx` dans les sources panels.
  2. `panels/build` → validé + compilé + enregistré.
  3. `panels/index` liste le nouveau panel.
  4. On l'ajoute au layout (`addPanel` → `panels/build` si absent) → rechargé.

### 13.5 Rechargement à chaud d'un panel modifié

- `panels/build` recompile ; le frontend détecte la version changée et
  re-importe le module (cache-busting sur le fichier).

---

## 14. Définition de "fait" (MVP de la GUI v2)

- [ ] Boot : vérifie le superviseur, charge la session, ouvre ses fenêtres.
- [ ] Layout : arbre split + groupes d'onglets, **tout panel dans un onglet**
      (invariants vérifiés), mutations → persistance temps réel + re-résolution
      du menu.
- [ ] 2 panels de base : `ressources`, `etat-systeme` (essentiels), avec contrat
      complet (menu à insérer, params, i18n, themeCss, cycle de vie).
- [ ] Un seul menu global, régénéré après chaque résolution, fusion par `path`.
- [ ] Slip views : panel `slip` avec sous-arbre + menu conditionnel
      (n'affiché que si la slip view est active) + prévisualisation au survol.
- [ ] Drag & drop des onglets : réordonner, déplacer entre groupes/fenêtres,
      split par bord, **extraction en vraie fenêtre** (drop hors fenêtres),
      annulation par clic droit / Echap.
- [ ] Sessions : chargement + switch.
- [ ] Multilingue : clés + fichiers lang (fr + en) + switch de langue.
- [ ] Thèmes : **vrai CSS** avec 2 composantes (global + panel), 3 niveaux
      d'application (panel / fenêtre / session) en cascade, thèmes génériques
      (dark/light/high-contrast) + custom, `theme/list|get|save`, persistance.
- [ ] Panels externes : contrat validé, compilation à chaud (`panels/build`),
      chargement paresseux, React partagé.
- [ ] Inspector : inspect DOM + actions (click/hover/type/drag) ciblées par
      fenêtre, sans screenshot.
- [ ] Tests : opérations de layout en unitaire (pures, invariants onglets),
      flux inspect/act en e2e.

---

## 15. Ce qu'on ne reprend PAS de la v1

- `useApp.ts` (1446 lignes, état global monolithique, `panelTree` ancien).
- `components/PanelTreeRenderer` + `TabbedPanel` (anciens, orphelins).
- Les panels conteneurs `100vh` (DashboardPanel, SystemDashboardPanel).
- Le double menu (menu du layout + menu injecté).
- Le split via `DragEvent` natif (crash WebKitGTK headless).
- Les thèmes en JSON non typés → on passe en variables CSS propres.
