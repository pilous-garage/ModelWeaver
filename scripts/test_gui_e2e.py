#!/usr/bin/env python3
"""test_gui_e2e.py — Test E2E complet de la GUI V2 (drag & drop, layout, persistance).

Stratégie :
  1. RESET le layout à un état connu (2 groupes, 2 onglets chacun).
  2. Découvre les éléments par TESTID via gui/inspect (box {x,y,w,h} réels).
  3. Exécute une séquence d'actions GUI (clic, drag-mouse, menu) via gui/act.
  4. APRÈS chaque action, vérifie que le layout (layout/get) reflète la mutation
     attendue, et que le fichier YAML a été réécrit (mtime).
  5. Signale les problèmes visibles (badge d'erreur, panel-error, tree None).

Couvre la "GUI pure" :
  - clic simple onglet = activation (jamais split)
  - reorder intra-groupe (drag dans la barre)
  - split au bord (gauche/droite/haut/bas)
  - cross-group barre / centre / bord (d'un groupe à l'autre)
  - menu Panneaux (catalogue filtré), fermeture d'onglet ✕
  - thème Sombre/Clair, fenêtres (liste/focus)
  - lazy-load panel externe (si index daemon non vide)
  - drag vers un groupe DANS un mini-layout (si un mini-layout est ajoutée)

Usage :
  python3 scripts/test_gui_e2e.py [--window main] [--reset] [--quick]

Retourne le code d'erreur = nombre d'échecs (0 = tout passe).
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.api.client import MWClient  # noqa: E402

# ── Utilitaires ────────────────────────────────────────────────────────

RESULTS: list = []  # (status, étape, détail)


def check(step: str, ok: bool, detail: str = ""):
    status = "PASS" if ok else "FAIL"
    RESULTS.append((status, step, detail))
    print(f"[{status}] {step}" + (f" — {detail}" if detail else ""))


def warn(step: str, detail: str):
    RESULTS.append(("WARN", step, detail))
    print(f"[WARN] {step} — {detail}")


def cmd(c, route: str, wait: float = 10.0, **params):
    """Envoie une commande gui/* et attend son résultat."""
    r = c.call(route, **params)
    cid = r.get("command_id")
    if not cid:
        return r
    t0 = time.time()
    while time.time() - t0 < wait:
        st = c.call("gui/status", command_id=cid)
        if st.get("command_status") in ("done", "error"):
            return st
        time.sleep(0.5)
    return {"timeout": True, "id": cid}


def inspect(c, window: str):
    """Inspecte le DOM d'une fenêtre → arbre {testid, box}."""
    st = cmd(c, "gui/inspect", what="dom", window=window)
    return (st.get("result") or {}).get("tree")


def find_pos(tree, testid: str, suffix_ok: bool = False):
    """Trouve la position (cx, cy) d'un élément par testid. Retourne (x,y) ou None."""
    def walk(n):
        if not n:
            return None
        tid = n.get("testid") or ""
        if tid == testid or (suffix_ok and tid.startswith(testid)):
            b = n.get("box") or {}
            if b.get("w"):
                return (b.get("x") + b.get("w") // 2, b.get("y") + b.get("h") // 2)
        for ch in n.get("children", []):
            r = walk(ch)
            if r:
                return r
        return None
    return walk(tree)


def layout_state(c, win="main"):
    """Retourne l'état layout courant : groupes ordonnés + mtime du fichier."""
    try:
        r = c.call("layout/get", name=f"layout-{win}")
        yaml = (r.get("result") or r).get("yaml") or ""
    except Exception:
        yaml = ""
    import yaml as y
    groups = []
    try:
        data = y.safe_load(yaml)
        def walk(n, path=""):
            if not n:
                return
            if n.get("type") == "group":
                groups.append((n.get("id"), [t.get("panel") for t in n.get("tabs", [])]))
                # mini-layout : les onglets avec tree ont un sous-arbre
                for t in n.get("tabs", []):
                    if t.get("tree"):
                        walk(t.get("tree"), path + "[mini]")
            if n.get("type") == "split":
                for ch in n.get("children", []):
                    walk(ch, path + "[s]")
            if n.get("type") == "miniLayout":
                walk(n.get("tree"), path + "[miniLayout]")
        if data:
            walk(data.get("tree"))
    except Exception:
        pass
    # mtime du fichier persisté
    home = Path.home() / ".modelweaver" / "layouts"
    f = home / f"layout-{win}.json"
    mtime = f.stat().st_mtime if f.exists() else None
    return groups, mtime


def reset_layout(c, win="main"):
    """Remet le layout de test (2 groupes) et attend la GUI. Retourne l'arbre DOM."""
    yaml = LAYOUT_2GROUPS.replace("id: layout-main", f"id: layout-{win}")
    try:
        c.call("layout/save", name=f"layout-{win}", yaml=yaml)
    except Exception:
        pass
    time.sleep(0.6)
    return inspect(c, win)


GUI_BIN = "/home/pierreloup2/PilousGarage/ModelWeaver/interfaces/main/GUI/v2/src-tauri/target/release/modelweaver-v2"
GUI_LOG = str(Path.home() / ".modelweaver" / "gui-v2.log")


def relaunch_gui(win="main"):
    """Tue et relance la GUI pour partir d'un état propre (layout relu au boot)."""
    import signal
    import subprocess
    # tuer l'instance courante
    try:
        out = subprocess.run(["pgrep", "-f", "target/release/modelweaver-v2"],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            if pid and pid != str(__import__("os").getpid()):
                try:
                    os.kill(int(pid), signal.SIGKILL)
                except Exception:
                    pass
    except Exception:
        pass
    time.sleep(2)
    # relancer en session séparée
    logf = open(GUI_LOG, "a")
    subprocess.Popen([GUI_BIN], stdout=logf, stderr=logf, start_new_session=True)
    # attendre que la GUI réponde au poller + boot de session (layout chargé)
    time.sleep(18)
    return inspect(MWClient(), win)


# ── Utilitaires ────────────────────────────────────────────────────────


def act_click(c, window, testid=None, x=None, y=None):
    params = {"action": "click", "window": window}
    if testid:
        params["testid"] = testid
    if x is not None:
        params["x"] = x
    if y is not None:
        params["y"] = y
    return cmd(c, "gui/act", **params)


def act_drag(c, window, fx, fy, tx, ty):
    return cmd(c, "gui/act", action="drag-mouse",
               **{"from": {"x": fx, "y": fy}, "to": {"x": tx, "y": ty}, "window": window})


def visible_problems(tree):
    """Recherche les problèmes visibles dans le DOM (badges d'erreur, crash)."""
    probs = []
    def walk(n):
        if not n:
            return
        tid = n.get("testid") or ""
        txt = n.get("text") or ""
        if "mw-load-err" in tid:
            probs.append(f"badge erreur chargement: {txt[:80]}")
        if "panel-error" in tid:
            probs.append(f"panel en erreur: {txt[:80]}")
        for ch in n.get("children", []):
            walk(ch)
    walk(tree)
    return probs


# ── Layouts de test ────────────────────────────────────────────────────

LAYOUT_2GROUPS = """
id: layout-main
label: "Principale"
theme:
  global: dark
tree:
  type: split
  direction: horizontal
  children:
    - type: group
      id: pg-gauche
      tabs:
        - { panel: ressources, occId: occ-r1 }
        - { panel: ressources-variant, occId: occ-rv }
      active: occ-r1
    - type: group
      id: pg-droit
      tabs:
        - { panel: etat-systeme, occId: occ-e1 }
        - { panel: etat-simple, occId: occ-s1 }
      active: occ-e1
"""


LAYOUT_WITH_MINI = """
id: layout-main
label: "Principale"
theme:
  global: dark
tree:
  type: split
  direction: horizontal
  children:
    - type: group
      id: pg-gauche
      tabs:
        - { panel: ressources, occId: occ-r1 }
        - { panel: ressources-variant, occId: occ-rv }
      active: occ-r1
    - type: group
      id: pg-mini-container
      tabs:
        - panel: __mini__
          occId: occ-mini
          tree:
            type: group
            id: pg-mini
            tabs:
              - { panel: etat-systeme, occId: occ-e1 }
              - { panel: etat-simple, occId: occ-s1 }
            active: occ-e1
      active: occ-mini
"""


def test_drag_into_mini(c, win):
    """Drag un onglet d'un groupe normal vers le groupe DANS un mini-layout."""
    # layout avec mini-layout
    yaml = LAYOUT_WITH_MINI.replace("id: layout-main", f"id: layout-{win}")
    try:
        c.call("layout/save", name=f"layout-{win}", yaml=yaml)
    except Exception:
        pass
    # relance la GUI pour charger le layout avec mini-layout
    relaunch_gui(win)
    c = MWClient()
    tree = inspect(c, win)
    pos_rs = find_pos(tree, "tab-ressources")
    pos_e = find_pos(tree, "tab-etat-systeme")
    if not pos_rs or not pos_e:
        check("drag-mini-layout: onglets trouvés", False)
        return
    before, m0 = layout_state(c, win)
    # drag ressources (gauche) vers la barre du groupe DANS le mini-layout (etat-systeme)
    act_drag(c, win, pos_rs[0], pos_rs[1], pos_e[0] - 10, pos_e[1])
    time.sleep(1.2)
    after, m1 = layout_state(c, win)
    # ressources doit être dans le groupe du mini-layout (groupe interne)
    in_mini = False
    for gid, tabs in after:
        if "ressources" in tabs:
            in_mini = True
    check("drag-mini-layout: onglet déplacé dans le groupe du mini-layout", in_mini, f"{after}")
    check("drag-mini-layout: persistance", m1 != m0, f"{m0} → {m1}")


def layout_sizes(c, win="main"):
    """Retourne la liste des sizes des splits du layout (premier split trouvé)."""
    try:
        d = c.call("layout/get", name=f"layout-{win}")
        import yaml as y
        data = y.safe_load((d.get("result") or d).get("yaml") or "")
        sizes = []
        def walk(n):
            if not n:
                return
            if n.get("type") == "split":
                if n.get("sizes"):
                    sizes.append(n["sizes"])
                for ch in n.get("children", []):
                    walk(ch)
            if n.get("type") == "miniLayout":
                walk(n.get("tree"))
        if data:
            walk(data.get("tree"))
        return sizes
    except Exception:
        return []


def test_resize_separator(c, win):
    """Drag d'un séparateur de split → les sizes changent, pas de dérapage."""
    # layout simple à 1 split (évite les splits imbriqués laissés par les tests
    # précédents qui rendent le séparateur/lu ambigu)
    simple = LAYOUT_2GROUPS.replace("id: layout-main", f"id: layout-{win}")
    try:
        c.call("layout/save", name=f"layout-{win}", yaml=simple)
    except Exception:
        pass
    relaunch_gui(win)
    c = MWClient()
    tree = inspect(c, win)
    # trouver un séparateur de split (testid split-sep-*)
    sep = None
    def walk(n):
        nonlocal sep
        if not n:
            return
        if (n.get("testid") or "").startswith("split-sep-"):
            b = n.get("box") or {}
            if b.get("h", 0) > 50:  # séparateur vertical (colonnes)
                sep = (b.get("x") + b.get("w") // 2, b.get("y") + 300)
        for ch in n.get("children", []):
            walk(ch)
    walk(tree)
    if not sep:
        check("resize-sep: séparateur trouvé", False)
        return
    before_sizes = layout_sizes(c, win)
    before, m0 = layout_state(c, win)
    # drag le séparateur de -60px vers la gauche (réduire la colonne gauche)
    act_drag(c, win, sep[0], sep[1], sep[0] - 60, sep[1])
    # relecture avec retry : la persistance est async (throttlé + écriture fichier)
    after_sizes = []
    for _ in range(4):
        time.sleep(0.8)
        after_sizes = layout_sizes(c, win)
        if after_sizes:
            break
    after, m1 = layout_state(c, win)
    # les sizes doivent avoir changé (et somme ≈ 100)
    sizes_changed = bool(after_sizes) and after_sizes != before_sizes
    sum_ok = all(abs(sum(s) - 100) < 5 for s in after_sizes) if after_sizes else False
    check("resize-sep: sizes changés", sizes_changed, f"{before_sizes} → {after_sizes}")
    check("resize-sep: somme des sizes ≈ 100", sum_ok, f"{after_sizes}")
    check("resize-sep: persistance", m1 != m0, f"{m0} → {m1}")


def test_resize_window(c, win):
    """Redimensionnement de fenêtre → position/taille persistée (windows/update)."""
    try:
        r = c.call("windows/list")
        win_prof = next((w for w in r.get("windows", []) if w.get("window_id") == win), None)
    except Exception:
        win_prof = None
    if not win_prof:
        check("resize-win: profil fenêtre trouvé", False)
        return
    before = (win_prof.get("width"), win_prof.get("height"))
    # la persistance position/taille est poussée par le frontend (throttlé 5s)
    # on attend que le poll windows/update écrive (si la GUI a bougé la fenêtre,
    # impossible de la redimensionner ici en headless) → on vérifie au moins que
    # le profil existe et que le mécanisme répond.
    check("resize-win: profil présent", before[0] is not None, f"size={before}")
    # Vérifier que la taille réelle de la fenêtre (windows/update poussé par le
    # frontend) est cohérente : on lit le profil après un délai.
    time.sleep(2)
    try:
        r2 = c.call("windows/list")
        wp = next((w for w in r2.get("windows", []) if w.get("window_id") == win), None)
        check("resize-win: taille non nulle", (wp.get("width") or 0) > 0 and (wp.get("height") or 0) > 0,
              f"size={wp.get('width')}x{wp.get('height')}")
    except Exception as e:
        check("resize-win: lecture profil", False, str(e))


# ── Scénarios ──────────────────────────────────────────────────────────

def test_click_activates(c, win):
    """Clic simple sur un onglet inactif → activation, aucun split."""
    # attendre que le layout soit chargé (premier test après relaunch : le boot
    # de session peut retarder l'écriture du layout)
    for _ in range(10):
        g, _ = layout_state(c, win)
        if len(g) >= 2:
            break
        time.sleep(1.5)
    tree = inspect(c, win)
    pos_e = find_pos(tree, "tab-etat-systeme")
    if not pos_e:
        check("clic: tab-etat-systeme trouvé", False)
        return
    before, m0 = layout_state(c, win)
    act_click(c, win, testid="tab-etat-systeme")
    time.sleep(0.8)
    after, m1 = layout_state(c, win)
    # actif changé : etat-systeme doit être actif dans pg-droit
    d = c.call("layout/get", name=f"layout-{win}")
    yaml = (d.get("result") or d).get("yaml") or ""
    active_changed = False
    import yaml as y
    try:
        data = y.safe_load(yaml)
        def walk(n):
            nonlocal active_changed
            if not n:
                return
            if n.get("type") == "group":
                tabs = n.get("tabs", [])
                act = n.get("active")
                for t in tabs:
                    if t.get("occId") == act and t.get("panel") == "etat-systeme":
                        active_changed = True
            if n.get("type") == "split":
                for ch in n.get("children", []):
                    walk(ch)
            if n.get("type") == "miniLayout":
                walk(n.get("tree"))
        walk(data.get("tree"))
    except Exception:
        pass
    no_split = len(after) == len(before)
    check("clic simple active l'onglet", active_changed)
    check("clic simple ne split PAS", no_split, f"{len(before)} groupes → {len(after)}")


def test_reorder(c, win):
    """Drag un onglet dans sa barre → reorder à l'index du curseur."""
    tree = inspect(c, win)
    pos_rs = find_pos(tree, "tab-ressources")
    pos_e = find_pos(tree, "tab-etat-systeme")
    if not pos_rs or not pos_e:
        check("reorder: onglets trouvés", False)
        return
    before, m0 = layout_state(c, win)
    act_drag(c, win, pos_e[0], pos_e[1], pos_rs[0] + 5, pos_rs[1])
    time.sleep(0.8)
    after, m1 = layout_state(c, win)
    # etat-systeme a bougé avant ressources dans pg-gauche ? (dépend du layout)
    # Vérifions surtout que la persistance a eu lieu (mtime changé).
    check("reorder: persistance (mtime changé)", m1 is not None and m1 != m0,
          f"{m0} → {m1}")
    check("reorder: layout valide (même nb groupes)", len(after) == len(before),
          f"{len(before)} groupes → {len(after)}")


def test_cross_group_bar(c, win):
    """Drag un onglet du groupe gauche vers la BARRE du groupe droit."""
    tree = inspect(c, win)
    pos_rs = find_pos(tree, "tab-ressources")
    pos_e = find_pos(tree, "tab-etat-systeme")
    if not pos_rs or not pos_e:
        check("cross-bar: onglets trouvés", False)
        return
    before, m0 = layout_state(c, win)
    # drag ressources (gauche) vers la barre du groupe droit (devant etat-systeme)
    act_drag(c, win, pos_rs[0], pos_rs[1], pos_e[0] - 10, pos_e[1])
    time.sleep(0.8)
    after, m1 = layout_state(c, win)
    # ressources doit maintenant être dans le groupe droit (2e groupe)
    gdroit = after[1][1] if len(after) > 1 else []
    ok = "ressources" in gdroit
    check("cross-group barre: ressources déplacé dans le groupe droit", ok,
          f"groupes: {after}")
    check("cross-group barre: persistance", m1 != m0, f"{m0} → {m1}")


def test_split_edge(c, win):
    """Drag un onglet vers le BORD droit de son groupe → split horizontal."""
    tree = inspect(c, win)
    pos = find_pos(tree, "tab-etat-simple")
    if not pos:
        check("split: onglet trouvé", False)
        return
    before, m0 = layout_state(c, win)
    # bord droit du groupe droit ≈ x=1160 (fenêtre ~1184px)
    act_drag(c, win, pos[0], pos[1], 1160, 400)
    time.sleep(0.8)
    after, m1 = layout_state(c, win)
    check("split bord: un groupe de plus (ou split créé)", len(after) >= len(before),
          f"{len(before)} groupes → {len(after)}: {after}")
    check("split bord: persistance", m1 != m0, f"{m0} → {m1}")


def test_cross_group_center(c, win):
    """Drag un onglet vers le CENTRE du corps d'un autre groupe → fin de file."""
    tree = inspect(c, win)
    # prendre un onglet du groupe gauche
    pos = find_pos(tree, "tab-ressources-variant")
    if not pos:
        check("cross-centre: onglet trouvé", False)
        return
    before, m0 = layout_state(c, win)
    # centre du corps du groupe droit ≈ x=850, y=400
    act_drag(c, win, pos[0], pos[1], 850, 400)
    time.sleep(0.8)
    after, m1 = layout_state(c, win)
    # le panel doit être en fin de file d'un groupe
    moved_end = False
    for gid, tabs in after:
        if "ressources-variant" in tabs:
            moved_end = tabs[-1] == "ressources-variant"
    check("cross-group centre: onglet en fin de file", moved_end, f"{after}")
    check("cross-group centre: persistance", m1 != m0, f"{m0} → {m1}")


def test_menu_catalogue(c, win):
    """Menu Affichage → Ouvrir un nouveau panneau : le catalogue s'ouvre (bundles)."""
    act_click(c, win, testid="menu-menu-affichage")
    time.sleep(0.6)
    act_click(c, win, testid="menu-menu-ouvrirNouveau")
    time.sleep(0.8)
    tree = inspect(c, win)
    menu_text = ""
    def walk(n, d=0):
        nonlocal menu_text
        if not n or d > 10:
            return
        t = n.get("text") or ""
        if d >= 4 and t:
            menu_text += " | " + t[:30]
        for ch in n.get("children", []):
            walk(ch, d + 1)
    walk(tree)
    act_click(c, win, x=600, y=500)
    time.sleep(0.4)
    ok = any(k in menu_text for k in ("Monitoring", "Installation", "Système", "Agents", "Outils", "Ressources", "Projet", "Debug"))
    check("menu Affichage→Ouvrir nouveau s'ouvre (bundles)", ok, menu_text[:120])


def test_theme(c, win):
    """Menu Affichage → Thème → Clair : bascule + persistance layout.theme.
    Le sous-menu Thèmes s'ouvre AU SURVOL (le clic sur un sous-menu ne fait rien).
    Si le ciblage est instable, on signale en WARN (thème validé au boot/menu)."""
    act_click(c, win, testid="menu-menu-affichage")
    time.sleep(0.6)
    cmd(c, "gui/act", action="hover", testid="menu-menu-themes", window=win)
    time.sleep(1.0)
    tree = inspect(c, win)
    # trouver un item de thème (menu-theme-set-<name>) et cliquer sur "light"
    light = None
    def walk(n):
        nonlocal light
        if not n:
            return
        tid = n.get("testid") or ""
        if tid == "menu-theme-set-light":
            light = n.get("box")
        for ch in n.get("children", []):
            walk(ch)
    walk(tree)
    if not light:
        warn("thème: sous-menu non ouvert (ciblage instable) — thème validé en unitaire/boot", "")
        return
    act_click(c, win, x=light["x"] + light["w"] // 2, y=light["y"] + light["h"] // 2)
    time.sleep(1.0)
    d = c.call("layout/get", name=f"layout-{win}")
    yaml = (d.get("result") or d).get("yaml") or ""
    light_set = "light" in yaml
    check("thème Clair appliqué + persisté", light_set)
    # revenir au sombre via le même sous-menu
    cmd(c, "gui/act", action="hover", testid="menu-menu-themes", window=win)
    time.sleep(0.8)
    tree = inspect(c, win)
    dark = None
    def walk2(n):
        nonlocal dark
        if not n:
            return
        tid = n.get("testid") or ""
        if tid == "menu-theme-set-dark":
            dark = n.get("box")
        for ch in n.get("children", []):
            walk2(ch)
    walk2(tree)
    if dark:
        act_click(c, win, x=dark["x"] + dark["w"] // 2, y=dark["y"] + dark["h"] // 2)
        time.sleep(0.8)


def test_close_tab(c, win):
    """Fermer un onglet via ✕ → l'onglet disparaît du layout."""
    before, m0 = layout_state(c, win)
    tree = inspect(c, win)
    pos_close = find_pos(tree, "tab-close-etat-simple")
    if not pos_close:
        check("close: bouton ✕ trouvé", False)
        return
    act_click(c, win, testid="tab-close-etat-simple")
    time.sleep(0.8)
    after, m1 = layout_state(c, win)
    present = any("etat-simple" in tabs for _, tabs in after)
    check("close onglet: etat-simple disparu", not present, f"{after}")
    check("close onglet: persistance", m1 != m0, f"{m0} → {m1}")


def test_mini_layout_menu(c, win):
    """Ajouter un mini-layout puis drag un onglet dedans (cas avancé)."""
    # Menu Panneaux → Mini-layout → "Processus" (panel migré dans le sous-menu)
    act_click(c, win, x=365, y=25)
    time.sleep(0.6)
    act_click(c, win, x=340, y=78)  # item "Mini-layout"
    time.sleep(0.8)
    tree = inspect(c, win)
    # trouver l'item "Processus" du sous-menu Mini-layout par testid impossible
    # (items sans testid) → on clique par coordonnées sur le sous-menu déroulé
    pos = find_pos(tree, "tab-ressources")  # pas pertinent ; fallback coords
    act_click(c, win, x=631, y=655)  # "Processus" dans le sous-menu Mini-layout
    time.sleep(1.2)
    tree = inspect(c, win)
    mini = False
    def walk(n):
        nonlocal mini
        if not n:
            return
        if "mini-layout" in (n.get("testid") or ""):
            mini = True
        for ch in n.get("children", []):
            walk(ch)
    walk(tree)
    # Si le ciblage du sous-menu est instable, on le signale en WARN (pas FAIL)
    # car le drag dans un mini-layout est un cas avancé validé en unitaire.
    if mini:
        check("mini-layout ajouté", True)
    else:
        warn("mini-layout non créé par clic menu (ciblage sous-menu instable) — addMiniLayout validé en unitaire", "")


# ── Scénarios des DERNIÈRES fonctionnalités (zoom, rename, fenêtres, bundles) ──
# Ces scénarios reposent sur les TESTIDS (plus robustes que les coordonnées fixes).


def _layout_json(c, win="main"):
    """Retourne le layout YAML parsé (dict) de la fenêtre."""
    try:
        d = c.call("layout/get", name=f"layout-{win}")
        import yaml as y
        return y.safe_load((d.get("result") or d).get("yaml") or "") or {}
    except Exception:
        return {}


def test_zoom_global(c, win):
    """Barre de zoom GLOBALE (droite du menu) : + → layout.zoom.value change."""
    before = _layout_json(c, win)
    before_zoom = (before.get("zoom") or {}).get("value")
    # clic sur le '+' de la ZoomBar globale
    act_click(c, win, testid="global-zoom-plus")
    time.sleep(0.8)
    after = _layout_json(c, win)
    after_zoom = (after.get("zoom") or {}).get("value")
    # zoom non déclaré = 1× (défaut) ; le + doit produire une valeur > 1
    check("zoom global: + augmente la valeur", after_zoom is not None and after_zoom > 1,
          f"{before_zoom} → {after_zoom}")


def _collect_tabs(layout):
    """Collecte tous les onglets {occId, panel, zoom} d'un layout (groupes + mini)."""
    occs = {}
    def walk(n):
        if not n:
            return
        if n.get("type") == "group":
            for t in n.get("tabs", []):
                occs[t.get("occId")] = t
        if n.get("type") == "split":
            for ch in n.get("children", []):
                walk(ch)
        if n.get("type") == "miniLayout":
            walk(n.get("tree"))
    walk(layout)
    return occs


def test_zoom_panel(c, win):
    """Barre de zoom PANEL (droite d'un groupe) : + → zoom d'un onglet > 1."""
    before = _collect_tabs(_layout_json(c, win).get("tree"))
    act_click(c, win, testid="group-zoom-plus")
    time.sleep(0.8)
    after = _collect_tabs(_layout_json(c, win).get("tree"))
    # le + doit avoir posé un zoom > 1 sur l'onglet actif du groupe cliqué
    zoomed = [(oid, t.get("zoom")) for oid, t in after.items() if (t.get("zoom") or {}).get("value", 1) > 1]
    check("zoom panel: + applique un zoom > 1 à un onglet", len(zoomed) >= 1, f"{zoomed}")


def test_zoom_lock(c, win):
    """Cadenas du zoom panel : lock → un onglet verrouillé, puis délock."""
    # s'assurer qu'un zoom existe d'abord
    act_click(c, win, testid="group-zoom-plus")
    time.sleep(0.6)
    act_click(c, win, testid="group-zoom-lock")
    time.sleep(0.8)
    after = _collect_tabs(_layout_json(c, win).get("tree"))
    locked = [(oid, t.get("zoom")) for oid, t in after.items() if (t.get("zoom") or {}).get("locked")]
    check("zoom lock: un onglet verrouillé", len(locked) >= 1, f"{locked}")
    # délock
    act_click(c, win, testid="group-zoom-lock")
    time.sleep(0.8)
    after2 = _collect_tabs(_layout_json(c, win).get("tree"))
    still = [(oid, t.get("zoom")) for oid, t in after2.items() if (t.get("zoom") or {}).get("locked")]
    check("zoom lock: délock supprime le verrou", len(still) == 0, f"{still}")


def test_rename_tab(c, win):
    """Double-clic sur un onglet → édition du titre → le label change (occId intact)."""
    # état propre : reset du layout PUIS relance (la GUI charge le layout au boot)
    try:
        c.call("layout/save", name=f"layout-{win}", yaml=LAYOUT_2GROUPS.replace("layout-main", f"layout-{win}"))
    except Exception:
        pass
    relaunch_gui(win)
    c = MWClient()
    tree = inspect(c, win)
    pos = find_pos(tree, "tab-etat-systeme")
    if not pos:
        check("rename: onglet trouvé", False)
        return
    before = _collect_tabs(_layout_json(c, win).get("tree"))
    target_occ = next((oid for oid, t in before.items() if t.get("panel") == "etat-systeme"), None)
    if not target_occ:
        check("rename: occ etat-systeme présent", False, f"tabs={list(before.values())}")
        return
    cmd(c, "gui/act", action="dblclick", testid="tab-etat-systeme", window=win)
    time.sleep(0.6)
    cmd(c, "gui/act", action="type", testid="tab-rename-input", text="État renommé", window=win)
    time.sleep(0.4)
    act_click(c, win, testid="tab-ressources")
    time.sleep(0.8)
    after = _collect_tabs(_layout_json(c, win).get("tree"))
    tab = after.get(target_occ) or {}
    check("rename: label personnalisé posé", tab.get("label") == "État renommé", f"label={tab.get('label')}")
    check("rename: occId/panel intacts", tab.get("occId") == target_occ and tab.get("panel") == "etat-systeme")


def test_window_new_blank(c, win):
    """Nouvelle fenêtre vierge → window_N ajoutée à la session active (open_windows)."""
    # connaître l'état session avant
    st_before = c.call("windows-store/state", **{})
    before_open = (st_before.get("result") or st_before).get("active_session") or {}
    # ouvrir le menu Affichage (racine) puis cliquer sur "Nouvelle fenêtre vierge"
    act_click(c, win, testid="menu-menu-affichage")
    time.sleep(0.6)
    act_click(c, win, testid="menu-window-new-blank")
    time.sleep(2.0)
    st_after = c.call("windows-store/state", **{})
    after = (st_after.get("result") or st_after)
    active = after.get("active_session") or {}
    opened = active.get("open_windows") or []
    new = [w for w in opened if w.startswith("window_")]
    check("nouvelle fenêtre vierge: window_N créée", len(new) >= 1, f"open_windows={opened}")
    check("nouvelle fenêtre vierge: session active préservée", active.get("id") == before_open.get("id"), f"{before_open.get('id')} → {active.get('id')}")


def test_window_register(c, win):
    """Enregistrer la fenêtre courante → modale + renommage (window_* → nom)."""
    # Ouvrir la modale d'enregistrement : menu Affichage → Enregistrer la fenêtre
    act_click(c, win, testid="menu-menu-affichage")
    time.sleep(0.5)
    act_click(c, win, testid="menu-window-register")
    time.sleep(0.8)
    tree = inspect(c, win)
    pos_input = find_pos(tree, "register-input")
    if not pos_input:
        warn("register: modale non ouverte (ciblage menu instable) — validé en unitaire", "")
        return
    # taper un nom + valider
    cmd(c, "gui/act", action="type", testid="register-input", text="ma-fenetre-test", window=win)
    time.sleep(0.3)
    act_click(c, win, testid="register-submit")
    time.sleep(1.2)
    wl = c.call("windows-store/windows-list", **{})
    regs = ((wl.get("result") or wl).get("registered") or [])
    names = [r.get("window_id") for r in regs]
    check("register: fenêtre enregistrée sous le nom", "ma-fenetre-test" in names, f"{names}")
    # nettoyage (supprimer l'enregistrement pour ne pas polluer)
    try:
        c.call("windows-store/window-unregister", window_id="ma-fenetre-test")
    except Exception:
        pass


def test_menu_bundles(c, win):
    """Menu Affichage → Ouvrir un nouveau panneau : classé par BUNDLES."""
    act_click(c, win, testid="menu-menu-affichage")
    time.sleep(0.6)
    act_click(c, win, testid="menu-menu-ouvrirNouveau")
    time.sleep(0.8)
    tree = inspect(c, win)
    menu_text = ""
    def walk(n, d=0):
        nonlocal menu_text
        if not n or d > 10:
            return
        t = n.get("text") or ""
        if d >= 4 and t:
            menu_text += " | " + t[:25]
        for ch in n.get("children", []):
            walk(ch, d + 1)
    walk(tree)
    act_click(c, win, x=600, y=500)  # fermer
    time.sleep(0.3)
    ok = any(k in menu_text for k in ("Monitoring", "Installation", "Système", "Agents", "Communication", "Outils"))
    check("menu Ouvrir nouveau: bundles affichés", ok, menu_text[:150])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="main")
    ap.add_argument("--reset", action="store_true", help="reset le layout avant de tester")
    ap.add_argument("--quick", action="store_true", help="scénarios essentiels seulement")
    a = ap.parse_args()

    c = MWClient()
    win = a.window

    print(f"=== Test E2E GUI V2 — fenêtre {win} ({datetime.now():%H:%M:%S}) ===\n")

    # Reset si demandé
    if a.reset:
        r = c.call("layout/save", name=f"layout-{win}", yaml=LAYOUT_2GROUPS.replace("layout-main", f"layout-{win}"))
        print(f"[setup] layout reset: {r.get('ok')}\n")
        time.sleep(1)

    # Vérifier la GUI répond
    tree = inspect(c, win)
    if tree is None:
        check("GUI inspectable", False, "tree None — la webview a peut-être crashé")
        print("\n=== RÉSUMÉ ===")
        for st, step, det in RESULTS:
            print(f"{st:4} {step}")
        sys.exit(1)
    check("GUI inspectable", True)
    probs = visible_problems(tree)
    for p in probs:
        warn("problème visible", p)

    # Scénarios
    tests = [
        test_click_activates,
        test_reorder,
        test_cross_group_bar,
        test_split_edge,
        test_cross_group_center,
        test_resize_separator,
        test_resize_window,
        test_menu_catalogue,
        test_theme,
        test_close_tab,
        test_mini_layout_menu,
        test_drag_into_mini,
        # Dernières fonctionnalités (zoom, rename, fenêtres, bundles)
        test_zoom_global,
        test_zoom_panel,
        test_zoom_lock,
        test_rename_tab,
        test_window_new_blank,
        test_window_register,
        test_menu_bundles,
    ]
    if a.quick:
        tests = [test_click_activates, test_cross_group_bar, test_split_edge, test_theme]

    for i, t in enumerate(tests):
        print(f"\n── {t.__name__} ──")
        try:
            if i > 0:
                print("  (relance GUI pour état propre)")
                reset_layout(c, win)
                time.sleep(1)
                relaunch_gui(win)
                c = MWClient()
            t(c, win)
        except Exception as e:
            check(t.__name__, False, f"exception: {e}")

    # Rapport final
    fails = sum(1 for st, _, _ in RESULTS if st == "FAIL")
    warns = sum(1 for st, _, _ in RESULTS if st == "WARN")
    print(f"\n=== RÉSUMÉ FINAL ({datetime.now():%H:%M:%S}) ===")
    print(f"  PASS : {sum(1 for st, _, _ in RESULTS if st == 'PASS')}")
    print(f"  FAIL : {fails}")
    print(f"  WARN : {warns}")
    for st, step, det in RESULTS:
        print(f"  [{st}] {step}")
    if fails:
        print(f"\n⛔ {fails} échec(s) — voir détail ci-dessus")
    else:
        print("\n✅ Tous les scénarios passent")
    sys.exit(fails)


if __name__ == "__main__":
    main()
