// App — composant racine d'une fenêtre.
// Gère le layout (source de vérité), la résolution, le menu, le rendu.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Layout, MenuItem, PanelOcc } from './layout/types.ts';
import { resolveLayout, listGroups } from './layout/resolve.ts';
import * as ops from './layout/ops.ts';
import { findGroup } from './layout/ops.ts';
import { persistLayout } from './layout/persist.ts';
import { MenuBar } from './components/MenuBar.tsx';
import { SplitTree, type RenderCtx } from './components/SplitTree.tsx';
import { MiniLayoutPanel } from './components/MiniLayoutPanel.tsx';
import { ZoomBar } from './components/ZoomBar.tsx';
import { groupAt } from './dragStore.ts';
import { nextZoom as zoomNext, prevZoom as zoomPrev } from './zoom.ts';
import { PanelBoundary } from './components/PanelBoundary.tsx';
import { getPanel, PANEL_REGISTRY, listPanels } from './panels/registry.ts';
import { useRefreshCount, refreshAllPanels } from './panels/refreshStore.ts';
import { listExternalCandidates, ensurePanelLoaded, getAllPanelStatus } from './panels/loader.ts';
import { daemonPost, daemonPostStream } from './bridge.ts';
import { startGuiInspectorPoll } from './guiInspector.ts';
import { t, setLocale, persistLocale, useLocale } from './i18n.ts';
import { applyTheme, listThemes, getCurrentTheme } from './theme.ts';
import type { ThemeDef } from './theme.ts';
import {
  useWindowsStore, startWindows, pushCurrentWindowState,
  openWindow, focusWindow, closeWindow, closeCurrentWindow,
  toggleFullscreen, saveCurrentWindow, layoutNameFor, listWindowSources,
  toggleFullscreenSession,
  newBlankWindow, focusOrOpenWindow, resetWindowLayout,
  createSession, activateSession, openSession, closeSession, deleteSession,
  switchSession, closeSessionWindows,
  registerWindow, renameSession, setSessionTheme, persistWindowThemeLock,
} from './windows.ts';
import { Welcome } from './components/Welcome.tsx';
import { RegisterWindowModal, SessionModal, AboutModal } from './components/WindowModals.tsx';
import type { PanelContext } from './panels/contract.ts';

function getLocaleAndApply(loc: string) {
  setLocale(loc === 'en' ? 'en' : 'fr');
  persistLocale(loc === 'en' ? 'en' : 'fr', daemonPost).catch(() => {});
}

/**
 * Injecte dans le menu « Fenêtre » les sous-menus dynamiques :
 *  - fenêtres ouvertes (liste, • = fenêtre courante) → window:focus:<id>
 *  - ouvrir (templates) → window:open:<template>
 *  - puis split/fermer/enregistrer (actions statiques déjà présentes)
 * Et ajoute « Ouverts dans une fenêtre » (panels des autres fenêtres).
 * Injecte aussi le sous-menu « Affichage → Thème » (dark/light/externes).
 */
function injectWindowMenu(menu: MenuItem[], winState: ReturnType<typeof useWindowsStore>, windowId: string, themes: ThemeDef[], themeLocked = false, refreshCount = 0): MenuItem[] {
  const { profiles, liveLabels } = winState;
  const next = menu.map((m) => ({ ...m, items: m.items ? [...m.items] : m.items }));

  // ── Fenêtres OUVERTES (menu Affichage → Fenêtres) ──────────────────
  // On greffe la liste des fenêtres vivantes (profil persisté + live) en tête
  // du sous-menu « Ouvrir une fenêtre ». Clic = focus + surbrillance du cadre.
  const aff = next.find((m) => m.labelKey === 'menu.affichage');
  if (aff && aff.items) {
    const openItem = aff.items.find((it) => it.labelKey === 'menu.ouvrirFenetre');
    if (openItem && openItem.items) {
      const liveItems: MenuItem[] = liveLabels
        .filter((id) => id !== windowId)
        .map((id) => ({
          labelKey: (profiles.find((p) => p.window_id === id)?.title) || id,
          suffix: '●',
          action: `window:focus-or-open:${id}`,
        }));
      if (liveItems.length) {
        openItem.items = [
          { labelKey: 'menu.fenetresOuvertes', items: liveItems },
          { type: 'separator' },
          ...openItem.items,
        ];
      }
    }

    // ── Refresh (manuel) : rempli si des panels sont enregistrés ─────
    aff.items = aff.items
      .map((it) => {
        if (it.action === 'menu:refresh-placeholder') {
          return refreshCount > 0
            ? {
                labelKey: 'menu.refresh',
                items: [
                  { labelKey: 'menu.refreshTout', action: 'menu:refresh-all' },
                ],
              }
            : { labelKey: 'menu.refresh', disabled: true, items: [] };
        }
        return it;
      })
      .filter((it) => it.action !== 'menu:refresh-placeholder' || refreshCount > 0);

    // ── Thèmes : remplace « Thèmes » par le sous-menu réel (session + fenêtre) ──
    aff.items = aff.items.map((it) => {
      if (it.action === 'theme:set') {
        const sessionTheme = winState.activeSession?.theme;
        return {
          labelKey: 'menu.themes',
          items: [
            { labelKey: 'menu.themeSession', disabled: true, style: 'section-header' as any },
            ...themes.map((th) => ({
              labelKey: th.label ?? th.name,
              action: `theme:session:${th.name}`,
              checked: th.name === sessionTheme,
            })),
            { type: 'separator' },
            { labelKey: 'menu.themeFenetre', disabled: true, style: 'section-header' as any },
            {
              labelKey: 'menu.themeLock',
              checked: themeLocked,
              action: 'theme:lock-toggle',
            },
            ...themes.map((th) => ({
              labelKey: th.label ?? th.name,
              action: `theme:set:${th.name}`,
              checked: th.name === getCurrentTheme(),
            })),
          ],
        };
      }
      return it;
    });
  }

  return next;
}

interface Props {
  layout: Layout;
  windowId: string;
  onChange?(layout: Layout): void;
}

// Broadcast du thème de SESSION : quand une fenêtre change le thème de session,
// les autres fenêtres l'appliquent, SAUF si elles sont en theme-lock.
const sessionThemeChannel: BroadcastChannel | null = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('mw-session-theme') : null;
if (sessionThemeChannel) {
  sessionThemeChannel.onmessage = (ev: MessageEvent) => {
    const theme = ev?.data?.theme;
    if (!theme) return;
    // une fenêtre LOCKÉE garde son thème (le lock est exposé sur window).
    if ((window as any).__MW_THEME_LOCKED__) return;
    applyTheme(theme).catch(() => {});
  };
}

export function App({ layout: initialLayout, windowId, onChange }: Props) {
  // Réactivité à la langue : le changement de locale re-rend ce composant (menu, labels).
  useLocale();
  const [layout, setLayout] = useState<Layout>(initialLayout);
  const [themes, setThemes] = useState<ThemeDef[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [showRegisterModal, setShowRegisterModal] = useState(false);
  const [showSessionModal, setShowSessionModal] = useState<{ session: any } | null>(null);
  const [showAbout, setShowAbout] = useState(false);
  const [themeLocked, setThemeLocked] = useState(false);
  const themeLockedRef = React.useRef(false);
  const winState = useWindowsStore();
  const refreshCount = useRefreshCount();

  // Surbrillance du CADRE de fenêtre : quand on a ciblé CETTE fenêtre depuis le
  // menu (highlightWindow == windowId), on affiche un outline pendant ~2,5s.
  const rootRef = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    const hl = winState.highlightWindow;
    const el = rootRef.current;
    if (!el) return;
    if (hl && hl === windowId) {
      el.style.outline = '3px solid var(--mw-accent, #3b82f6)';
      el.style.outlineOffset = '-3px';
    } else {
      el.style.outline = '';
      el.style.outlineOffset = '';
    }
  }, [winState.highlightWindow, windowId]);

  // Surbrillance d'un onglet/panel : effacée au PROCHAIN clic (n'importe où).
  useEffect(() => {
    if (!highlight) return;
    const clear = () => setHighlight(null);
    window.addEventListener('mousedown', clear, { once: true });
    return () => window.removeEventListener('mousedown', clear);
  }, [highlight]);

  // Liste des thèmes disponibles (builtin + daemon).
  useEffect(() => {
    listThemes().then(setThemes).catch(() => {});
  }, []);

  // Poller gui/* : permet au backend de piloter/inspecter CETTE fenêtre.
  useEffect(() => {
    startGuiInspectorPoll(1500);
  }, []);

  // Raccourci global F11 → plein écran.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'F11') {
        e.preventDefault();
        toggleFullscreen().catch(() => {});
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Multi-fenêtres : sync partagé (idempotent) + persistance position/taille.
  useEffect(() => {
    startWindows(windowId);
    const iv = setInterval(() => {
      pushCurrentWindowState(windowId).catch(() => {});
    }, 2500);
    return () => clearInterval(iv);
  }, [windowId]);

  // Résolution après chaque changement de layout.
  const resolved = useMemo(() => {
    const panelMenus: Record<string, any[]> = {};
    for (const p of PANEL_REGISTRY ? Object.values(PANEL_REGISTRY) : []) {
      if (p.menu?.length) panelMenus[p.id] = p.menu;
    }
    const catalogue = [
      ...listPanels().map((p) => ({ id: p.id, labelKey: p.labelKey, bundles: p.bundles })),
      // externes connus (pas encore chargés) — chargés à la demande
      ...listExternalCandidates()
        .filter((e) => !getPanel(e.id))
        .map((e) => ({ id: e.id, labelKey: e.labelKey ?? e.id })),
    ];
    const src = listWindowSources();
    const windowMenu = {
      official: (src.official ?? []).map((o) => ({ id: o.id, title: o.title, layout: o.layout, theme: o.theme, width: o.width ?? undefined, height: o.height ?? undefined })),
      registered: (src.registered ?? []).map((r) => ({ window_id: r.window_id, title: r.title })),
      live: (src.live ?? []).map((w) => ({ window_id: w.window_id, title: w.title })),
      sessions: (winState.sessions ?? []).map((s) => ({ id: s.id, name: s.name, theme: s.theme, open_windows: s.open_windows })),
      activeSession: winState.activeSession ? { id: winState.activeSession.id, name: winState.activeSession.name } : null,
      liveLabels: winState.liveLabels ?? [],
    };
    const r = resolveLayout(layout, panelMenus, undefined, catalogue, windowMenu);
    return { ...r, menu: injectWindowMenu(r.menu, winState, windowId, themes, themeLockedRef.current, refreshCount) };
  }, [layout, winState, windowId, themes, refreshCount]);

  // Persistance temps réel.
  const mutate = useCallback((fn: (l: Layout) => Layout) => {
    setLayout((prev) => {
      const next = fn(prev);
      onChange?.(next);
      persistLayout(next, { post: daemonPost });
      return next;
    });
  }, [onChange]);

  // ── Zoom clavier (Ctrl+±, Ctrl+0) sur le panel sous le curseur ──────
  const mouseRef = useRef<{ x: number; y: number }>({ x: 0, y: 0 });
  useEffect(() => {
    const onMove = (e: MouseEvent) => { mouseRef.current = { x: e.clientX, y: e.clientY }; };
    window.addEventListener('mousemove', onMove);
    return () => window.removeEventListener('mousemove', onMove);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Ctrl+± (et Ctrl+num +) → zoom panel sous le curseur ; Ctrl+0 → reset.
      if (!(e.ctrlKey || e.metaKey)) return;
      const isPlus = e.key === '+' || e.key === '=' || e.key === 'Add';
      const isMinus = e.key === '-' || e.key === 'Subtract';
      if (!isPlus && !isMinus && e.key !== '0') return;
      e.preventDefault();
      setLayout((prev) => {
        const g = groupAt(mouseRef.current.x, mouseRef.current.y);
        const active = g ? ops.getGroupActive(prev, g.id) : null;
        if (!active) {
          // hors panel → zoom GLOBAL
          const cur = prev.zoom?.value ?? 1;
          if (e.key === '0') return ops.resetGlobalZoom(prev);
          return ops.setGlobalZoom(prev, isPlus ? zoomNext(cur) : zoomPrev(cur));
        }
        const local = ops.occZoom(active);
        if (e.key === '0') return ops.resetZoom(prev, active.occId);
        return ops.setZoomLocal(prev, active.occId, isPlus ? zoomNext(local) : zoomPrev(local));
      });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // ctx panels (enrichi : compatible panels V1 externes qui attendent un ctx
  // avec daemonPost/t/windowId, en plus du contrat V2 api/layout/params).
  const buildCtx = useCallback((occ: PanelOcc): PanelContext & Record<string, any> => ({
    api: { post: daemonPost, stream: daemonPostStream },
    daemonPost,
    post: daemonPost,
    daemonPostStream,
    layout,
    occId: occ.occId,
    params: occ.params ?? {},
    windowId,
    t,
    onMenuAction: () => {},
    addTab: (g, panel, p) => mutate((l) => ops.addPanel(l, g, panel, p)),
    closeTab: (g, occId) => mutate((l) => ops.closeTab(l, g, occId)),
    activateTab: (g, occId) => mutate((l) => ops.activateTab(l, g, occId)),
    extractTabToWindow: (g, occId) => mutate((l) => ops.closeTab(l, g, occId)), // MVP : retire (fenêtre réelle plus tard)
    setPanelTheme: () => {},
  }), [layout, mutate, windowId]);

  // Ref pour casser la référence circulaire renderPanel ⇄ renderPanel (les
  // mini-layouts imbriqués se passent renderPanel via cette ref).
  const renderPanelRef = React.useRef<(occ: PanelOcc, ancestorFactor?: number) => React.ReactNode>(() => null);

  const renderPanel = useCallback((occ: PanelOcc, ancestorFactor = 1) => {
    // MINI-LAYOUT : l'onglet a un sous-arbre → on le rend au lieu d'un composant.
    if (occ.tree) {
      return (
        <MiniLayoutPanel occ={occ} windowId={windowId} t={t} mutate={mutate} renderPanel={renderPanelRef.current} zoomFactor={ancestorFactor} />
      );
    }
    const def = getPanel(occ.panel);
    if (!def) return <div className="mw-panel-error">Panel inconnu : {occ.panel}</div>;
    const C = def.component;
    const ctx = buildCtx(occ);
    return (
      <PanelBoundary panelId={occ.panel}>
        <C ctx={ctx} params={occ.params ?? {}} />
      </PanelBoundary>
    );
  }, [buildCtx, mutate, windowId, t]);

  renderPanelRef.current = renderPanel;

  const splitCtx = useMemo<RenderCtx>(() => ({
    windowId,
    t,
    highlight,
    onActivate: (g: string, o: string) => mutate((l: Layout) => ops.activateTab(l, g, o)),
    onClose: (g: string, o: string) => mutate((l: Layout) => ops.closeTab(l, g, o)),
    onMove: (from: string, to: string, o: string, idx?: number) => mutate((l: Layout) => ops.moveTab(l, from, to, o, idx)),
    onSplit: (g: string, dir: 'horizontal' | 'vertical', o: string, from?: string) => mutate((l: Layout) => ops.splitGroup(l, g, dir, o, from)),
    onExtract: (g: string, o: string) => mutate((l: Layout) => ops.closeTab(l, g, o)),
    onRename: (g: string, o: string, label: string | null) => mutate((l: Layout) => ops.renameTab(l, g, o, label)),
    onZoom: (g: string, o: string, localValue: number) => mutate((l: Layout) => ops.setZoomLocal(l, o, localValue)),
    onZoomLock: (g: string, o: string, ancestorFactor: number) => mutate((l: Layout) => ops.toggleZoomLock(l, o, ancestorFactor)),
    onResize: (sid: string, idx: number, sepPos: number, total: number) => mutate((l: Layout) => ops.resizeSplit(l, sid, idx, sepPos, total)),
    renderPanel,
  }), [mutate, windowId, t, renderPanel, highlight]);

  const handleMenuAction = useCallback((action: string) => {
    // Actions de layout (SPEC §6.2)
    if (action.startsWith('panel:toggle:')) {
      const panelId = action.slice('panel:toggle:'.length);
      // Présent (sans params) → ACTIVER son onglet (avec surbrillance) ;
      // absent → l'ajouter au premier groupe (lazy-load si externe).
      const present = resolved.panelOccurrences
        .filter((occ) => occ.panel === panelId && ops.paramsEqual(occ.params, undefined));
      if (present.length > 0) {
        const target = present[0].occId;
        mutate((l) => {
          const g = findGroup(l.tree, target);
          return g ? ops.activateTab(l, g.id, target) : l;
        });
        setHighlight(target);
        return;
      }
      const doAdd = () => mutate((l) => {
        // Ajoute au premier groupe si possible, sinon enracine.
        const groups = listGroups(l.tree);
        const target = groups[0]?.id ?? null;
        const next = ops.addPanel(l, target, panelId);
        // surligne l'occurrence du panel nouvellement ajouté (nouvel occId)
        const prevOccIds = new Set<string>();
        (function walkOccs(t: any) {
          if (t?.type === 'group') for (const tab of t.tabs) { prevOccIds.add(tab.occId); if (tab.tree) walkOccs(tab.tree); }
          else if (t?.type === 'split') for (const c of t.children) walkOccs(c);
        })(l.tree);
        let addedOcc: string | null = null;
        (function walkOccs2(t: any) {
          if (addedOcc) return;
          if (t?.type === 'group') for (const tab of t.tabs) { if (!prevOccIds.has(tab.occId) && tab.panel === panelId) { addedOcc = tab.occId; return; } if (tab.tree) walkOccs2(tab.tree); }
          else if (t?.type === 'split') for (const c of t.children) walkOccs2(c);
        })(next.tree);
        if (addedOcc) setHighlight(addedOcc);
        return next;
      });
      // Chargement paresseux : panel externe pas encore chargé → import()
      // dynamique avant l'ajout (échec = pas d'ajout).
      if (!getPanel(panelId)) {
        ensurePanelLoaded(panelId).then((def) => {
          if (def) { doAdd(); setLoadErr(null); }
          else {
            const st = getAllPanelStatus()[panelId];
            setLoadErr(`Échec de chargement '${panelId}': ${st?.error ?? 'inconnu'}`);
          }
        }).catch((e: any) => setLoadErr(String(e?.message ?? e)));
      } else {
        doAdd();
      }
    } else if (action.startsWith('panel:close:')) {
      const occId = action.slice('panel:close:'.length);
      mutate((l) => { const g = findGroup(l.tree, occId); return g ? ops.closeTab(l, g.id, occId) : l; });
    } else if (action === 'mini-layout:add') {
      // Ajoute un mini-layout (onglet conteneur vide) dans le premier groupe.
      mutate((l) => {
        const groups = listGroups(l.tree);
        const target = groups[0]?.id ?? null;
        return ops.addMiniLayout(l, target ?? '', 'ide');
      });
    } else if (action.startsWith('panel:activate:')) {
      const occId = action.slice('panel:activate:'.length);
      mutate((l) => { const g = findGroup(l.tree, occId); return g ? ops.activateTab(l, g.id, occId) : l; });
      setHighlight(occId);
    } else if (action === 'layout:save') {
      persistLayout(layout, { post: daemonPost });
      saveCurrentWindow(windowId, layoutNameFor(windowId)).catch(() => {});
    } else if (action === 'app:quit') {
      closeCurrentWindow(windowId).catch(() => {});
    } else if (action === 'window:new-blank') {
      newBlankWindow().then((id) => { if (id) setHighlight(id); }).catch(() => {});
    } else if (action.startsWith('window:focus-or-open:')) {
      const target = action.slice('window:focus-or-open:'.length);
      focusOrOpenWindow(target).then((id) => { if (id) setHighlight(id); }).catch(() => {});
    } else if (action === 'window:register') {
      setShowRegisterModal(true);
    } else if (action === 'window:reset-layout') {
      resetWindowLayout(windowId).catch(() => {});
    } else if (action === 'session:new') {
      // Nouvelle session : ferme les fenêtres de la session courante, crée la
      // nouvelle session, l'active, et ouvre une fenêtre d'accueil vierge.
      switchSession('__new__', windowId).then((opened) => {
        if (!opened.length) newBlankWindow().catch(() => {});
      }).catch(() => {});
    } else if (action.startsWith('session:open:')) {
      const sid = action.slice('session:open:'.length);
      switchSession(sid, windowId).catch(() => {});
    } else if (action.startsWith('session:rename:')) {
      const sid = action.slice('session:rename:'.length);
      const s = winState.sessions.find((x) => x.id === sid);
      setShowSessionModal({ session: s ?? null });
    } else if (action.startsWith('session:close:')) {
      const sid = action.slice('session:close:'.length);
      // Fermer une session ACTIVE : on ferme aussi ses fenêtres (sauf main).
      if (winState.activeSession?.id === sid) {
        closeSessionWindows(windowId).then(() => closeSession(sid)).catch(() => {});
      } else {
        closeSession(sid).catch(() => {});
      }
    } else if (action.startsWith('session:delete:')) {
      const sid = action.slice('session:delete:'.length);
      // Supprimer la session active : on ferme d'abord ses fenêtres.
      if (winState.activeSession?.id === sid) {
        closeSessionWindows(windowId).then(() => deleteSession(sid)).catch(() => {});
      } else {
        deleteSession(sid).catch(() => {});
      }
    } else if (action === 'window:new' || action.startsWith('window:open:')) {
      const templateId = action.startsWith('window:open:') ? action.slice('window:open:'.length) : 'default';
      openWindow(templateId).catch(() => {});
    } else if (action.startsWith('window:open-predefined:')) {
      // Ouvre une fenêtre PRÉENREGISTRÉE (profil avec layout nommé + thème) :
      // le label Tauri = window_id du profil → le boot chargera son layout.
      const preId = action.slice('window:open-predefined:'.length);
      const profile = winState.profiles.find((p) => p.window_id === preId);
      openWindow(profile?.template ?? 'default', {
        title: profile?.title || preId,
        label: preId,
        size: profile?.width && profile?.height ? { width: profile.width, height: profile.height } : undefined,
      }).catch(() => {});
    } else if (action.startsWith('window:focus:')) {
      const target = action.slice('window:focus:'.length);
      focusWindow(target).catch(() => {});
    } else if (action.startsWith('window:close:')) {
      const target = action.slice('window:close:'.length);
      if (target === windowId) closeCurrentWindow(windowId).catch(() => {});
      else closeWindow(target).catch(() => {});
    } else if (action === 'theme:lock-toggle') {
      setThemeLocked((prev) => {
        const next = !prev;
        themeLockedRef.current = next;
        (window as any).__MW_THEME_LOCKED__ = next;
        // Persiste theme_locked dans le profil vivant (best-effort).
        persistWindowThemeLock(windowId, next, getCurrentTheme()).catch(() => {});
        return next;
      });
    } else if (action.startsWith('theme:set:')) {
      const themeName = action.slice('theme:set:'.length);
      applyTheme(themeName).then(() => {
        // Persiste le thème dans le layout + profil de fenêtre (avec theme_locked).
        mutate((l) => ({ ...l, theme: { ...(l.theme ?? {}), global: themeName } }));
        const locked = themeLockedRef.current;
        persistWindowThemeLock(windowId, locked, themeName).catch(() => {});
      }).catch(() => {});
    } else if (action.startsWith('theme:session:')) {
      // Thème de SESSION : s'applique à toutes les fenêtres (broadcast), sauf
      // celles verrouillées. Persisté dans le .session.yaml.
      const themeName = action.slice('theme:session:'.length);
      const sid = winState.activeSession?.id;
      if (sid) setSessionTheme(sid, themeName).catch(() => {});
      applyTheme(themeName).then(() => {
        mutate((l) => ({ ...l, theme: { ...(l.theme ?? {}), global: themeName } }));
        try { sessionThemeChannel?.postMessage({ theme: themeName }); } catch { /* best-effort */ }
      }).catch(() => {});
    } else if (action.startsWith('lang:set:')) {
      const loc = action.slice('lang:set:'.length);
      getLocaleAndApply(loc);
    } else if (action === 'window:fullscreen') {
      // F11 : bascule la fenêtre ; si on SORT du plein écran, on sort AUSSI la
      // session (toutes les fenêtres reviennent en normal).
      toggleFullscreen().then((full) => {
        if (!full) toggleFullscreenSession(false).catch(() => {});
      }).catch(() => {});
    } else if (action === 'window:fullscreen-session') {
      toggleFullscreenSession(true).catch(() => {});
    } else if (action === 'window:fullscreen-exit') {
      toggleFullscreenSession(false).catch(() => {});
    } else if (action === 'menu:refresh-all') {
      refreshAllPanels();
    } else if (action === 'help:about') {
      setShowAbout(true);
    } else if (action === 'window:save') {
      persistLayout(layout, { post: daemonPost });
      saveCurrentWindow(windowId, layoutNameFor(windowId)).catch(() => {});
    }
    // Autres actions (theme:set…) : MVP.
  }, [mutate, layout, windowId, resolved]);

  const menuItems = resolved.menu;
  const globalZoom = layout.zoom?.value ?? 1;

  return (
    <div ref={rootRef} style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)', fontFamily: 'var(--mw-font-ui, sans-serif)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'stretch', flexShrink: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <MenuBar items={menuItems} t={t} onAction={handleMenuAction} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', padding: '0 6px', borderLeft: '1px solid var(--mw-border, #334155)', background: 'var(--mw-bg, #0f172a)' }}>
          <ZoomBar
            value={globalZoom}
            locked={layout.zoom?.locked}
            onChange={(v) => mutate((l) => ops.setGlobalZoom(l, v))}
            onToggleLock={() => mutate((l) => ops.toggleGlobalZoomLock(l))}
            testidPrefix="global-"
          />
        </div>
      </div>
      {loadErr && (
        <div data-testid="mw-load-err" style={{ background: '#7f1d1d', color: '#fecaca', padding: '2px 10px', fontSize: 11 }}>
          {loadErr}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, padding: 2 }}>
        {resolved.root && !(resolved.root.kind === 'group' && resolved.root.tabs.length === 0) ? (
          <SplitTree node={resolved.root} ctx={splitCtx} zoomFactor={globalZoom} />
        ) : (
          <Welcome t={t} onAction={handleMenuAction} />
        )}
      </div>

      {showRegisterModal && (
        <RegisterWindowModal
          windowId={windowId}
          currentTitle={windowId}
          registeredNames={(listWindowSources().registered ?? []).map((r) => r.window_id)}
          onClose={() => setShowRegisterModal(false)}
          onDone={() => setShowRegisterModal(false)}
        />
      )}
      {showSessionModal && (
        <SessionModal
          session={showSessionModal.session}
          onClose={() => setShowSessionModal(null)}
          onDone={(value) => {
            const s = showSessionModal.session;
            if (s) renameSession(s.id, value).catch(() => {});
            setShowSessionModal(null);
          }}
        />
      )}
      {showAbout && <AboutModal onClose={() => setShowAbout(false)} />}
    </div>
  );
}
