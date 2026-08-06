// App — composant racine d'une fenêtre.
// Gère le layout (source de vérité), la résolution, le menu, le rendu.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import type { Layout, MenuItem, PanelOcc } from './layout/types.ts';
import { resolveLayout, listGroups } from './layout/resolve.ts';
import * as ops from './layout/ops.ts';
import { findGroup } from './layout/ops.ts';
import { persistLayout } from './layout/persist.ts';
import { MenuBar } from './components/MenuBar.tsx';
import { SplitTree } from './components/SplitTree.tsx';
import { PanelBoundary } from './components/PanelBoundary.tsx';
import { getPanel, PANEL_REGISTRY, listPanels } from './panels/registry.ts';
import { listExternalCandidates, ensurePanelLoaded, getAllPanelStatus } from './panels/loader.ts';
import { daemonPost } from './bridge.ts';
import { startGuiInspectorPoll } from './guiInspector.ts';
import { t, setLocale, persistLocale, useLocale } from './i18n.ts';
import { applyTheme, listThemes, getCurrentTheme } from './theme.ts';
import type { ThemeDef } from './theme.ts';
import {
  useWindowsStore, startWindows, pushCurrentWindowState,
  openWindow, focusWindow, closeWindow, closeCurrentWindow,
  toggleFullscreen, saveCurrentWindow, layoutNameFor,
} from './windows.ts';
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
function injectWindowMenu(menu: MenuItem[], winState: ReturnType<typeof useWindowsStore>, windowId: string, themes: ThemeDef[]): MenuItem[] {
  const { profiles, templates, remote, currentId, ready, error, liveLabels } = winState;
  const next = menu.map((m) => ({ ...m, items: m.items ? [...m.items] : m.items }));

  // Fenêtres ouvertes (profil persisté + live)
  const openItems: MenuItem[] = profiles.map((p) => ({
    labelKey: `${p.window_id === currentId ? '• ' : ''}${p.title || p.window_id}`,
    action: `window:focus:${p.window_id}`,
    checked: p.window_id === currentId,
  }));
  // Diagnostic / état de chargement du store fenêtres.
  if (!ready) {
    openItems.unshift({ labelKey: '(chargement des fenêtres…)', disabled: true });
  } else if (profiles.length === 0) {
    openItems.unshift({ labelKey: error ? `(erreur: ${error})` : '(aucune fenêtre)', disabled: true });
  }

  const openTemplateItems: MenuItem[] = templates.map((tp) => ({
    labelKey: tp.label,
    action: `window:open:${tp.id}`,
  }));
  // Fenêtres PRÉENREGISTRÉES (profils persistés avec layout nommé, non vivantes) :
  // on peut les rouvrir directement (layout + thème associés).
  const predefined = profiles.filter((p) => p.layout && p.layout !== layoutNameFor(p.window_id) && !liveLabels.includes(p.window_id));
  const predefinedItems: MenuItem[] = predefined.map((p) => ({
    labelKey: p.title || p.window_id,
    action: `window:open-predefined:${p.window_id}`,
  }));

  const remoteItems: MenuItem[] = remote.map((r) => ({
    labelKey: r.title || r.windowId,
    items: r.present.length
      ? r.present.map((occ) => ({
          labelKey: occ.panel,
          action: `window:focus:${r.windowId}`,
        }))
      : [{ labelKey: 'menu.fenetreVide', disabled: true }],
  }));

  // Localise le menu Fenêtre et greffe les sous-menus en tête.
  const fen = next.find((m) => m.labelKey === 'menu.fenetre');
  if (fen && fen.items) {
    const openSub = [...openTemplateItems];
    if (predefinedItems.length) {
      openSub.push({ type: 'separator' });
      openSub.push(...predefinedItems);
    }
    const dynamic: MenuItem[] = [
      { labelKey: 'menu.fenetresOuvertes', items: openItems },
      { type: 'separator' },
      { labelKey: 'menu.ouvrir', items: openSub },
    ];
    if (remoteItems.length) {
      dynamic.push({ type: 'separator' });
      dynamic.push({ labelKey: 'menu.ouvertsDansFenetre', items: remoteItems });
    }
    fen.items = [...dynamic, ...fen.items];
  }

  // Localise le menu Affichage et remplace « Thèmes » par le sous-menu réel.
  const aff = next.find((m) => m.labelKey === 'menu.affichage');
  if (aff && aff.items) {
    aff.items = aff.items.map((it) => {
      if (it.action === 'theme:set') {
        return {
          labelKey: 'menu.themes',
          items: themes.map((th) => ({
            labelKey: th.label ?? th.name,
            action: `theme:set:${th.name}`,
            checked: th.name === getCurrentTheme(),
          })),
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

export function App({ layout: initialLayout, windowId, onChange }: Props) {
  // Réactivité à la langue : le changement de locale re-rend ce composant (menu, labels).
  useLocale();
  const [layout, setLayout] = useState<Layout>(initialLayout);
  const [activeMiniLayoutId, setActiveMiniLayoutId] = useState<string | null>(null);
  const [themes, setThemes] = useState<ThemeDef[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const winState = useWindowsStore();

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
      ...listPanels().map((p) => ({ id: p.id, labelKey: p.labelKey })),
      // externes connus (pas encore chargés) — chargés à la demande
      ...listExternalCandidates()
        .filter((e) => !getPanel(e.id))
        .map((e) => ({ id: e.id, labelKey: e.labelKey ?? e.id })),
    ];
    const r = resolveLayout(layout, panelMenus, activeMiniLayoutId, catalogue);
    return { ...r, menu: injectWindowMenu(r.menu, winState, windowId, themes) };
  }, [layout, activeMiniLayoutId, winState, windowId, themes]);

  // Persistance temps réel.
  const mutate = useCallback((fn: (l: Layout) => Layout) => {
    setLayout((prev) => {
      const next = fn(prev);
      onChange?.(next);
      persistLayout(next, { post: daemonPost });
      return next;
    });
  }, [onChange]);

  // ctx panels (enrichi : compatible panels V1 externes qui attendent un ctx
  // avec daemonPost/t/windowId, en plus du contrat V2 api/layout/params).
  const buildCtx = useCallback((occ: PanelOcc): PanelContext & Record<string, any> => ({
    api: { post: daemonPost },
    daemonPost,
    post: daemonPost,
    layout,
    params: occ.params ?? {},
    windowId,
    t,
    onMenuAction: () => {},
    addTab: (g, panel, p) => mutate((l) => ops.addPanel(l, g, panel, p)),
    closeTab: (g, occId) => mutate((l) => ops.closeTab(l, g, occId)),
    activateTab: (g, occId) => mutate((l) => ops.activateTab(l, g, occId)),
    activateMiniLayout: (occId) => setActiveMiniLayoutId(occId),
    closeMiniLayout: () => setActiveMiniLayoutId(null),
    extractTabToWindow: (g, occId) => mutate((l) => ops.closeTab(l, g, occId)), // MVP : retire (fenêtre réelle plus tard)
    setPanelTheme: () => {},
  }), [layout, mutate, windowId]);

  const renderPanel = useCallback((occ: PanelOcc) => {
    const def = getPanel(occ.panel);
    if (!def) return <div className="mw-panel-error">Panel inconnu : {occ.panel}</div>;
    const C = def.component;
    const ctx = buildCtx(occ);
    // Error boundary par panel : un crash de rendu (ex. panel V1 externe)
    // isole l'erreur au lieu de faire tomber toute l'application.
    return (
      <PanelBoundary panelId={occ.panel}>
        <C ctx={ctx} params={occ.params ?? {}} />
      </PanelBoundary>
    );
  }, [buildCtx]);

  // Rendu d'une mini-layout (panel conteneur avec sous-arbre). La mini-layout est
  // rendue par SplitTree qui fournit frame + titre + bouton de fermeture.
  const renderMiniLayout = useCallback((miniLayout: any, close?: () => void) => (
    <div className="mw-mini-layout" data-testid={`mini-layout-${miniLayout.id}`} style={{ height: '100%', border: '1px solid var(--mw-accent, #0f3460)', borderRadius: 8, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
      <div className="mw-mini-layout-title" style={{ padding: '4px 8px', fontSize: 12, fontWeight: 600, borderBottom: '1px solid var(--mw-border, #334155)', background: 'var(--mw-bg-panel, #1e293b)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span data-testid={`mini-layout-title-${miniLayout.id}`}>{miniLayout.title ?? 'Mini-layout'}</span>
        <span
          data-testid={`mini-layout-close-${miniLayout.id}`}
          onClick={(e) => { e.stopPropagation(); close?.(); }}
          style={{ color: '#64748b', fontSize: 11, cursor: 'pointer', padding: '0 4px' }}
          title="Fermer la mini-layout"
        >✕</span>
      </div>
      <div data-testid="mini-layout-content" style={{ flex: 1, minHeight: 0 }}>
        {miniLayout.children.map((c: any, i: number) => (
          <SplitTree key={i} node={c} ctx={{ windowId, t, onActivate: (g, o) => mutate((l) => ops.activateTab(l, g, o)), onClose: (g, o) => mutate((l) => ops.closeTab(l, g, o)), onMove: (from, to, o, idx) => mutate((l) => ops.moveTab(l, from, to, o, idx)), onSplit: (g, dir, o, from) => mutate((l) => ops.splitGroup(l, g, dir, o, from)), onExtract: (g, o) => mutate((l) => ops.closeTab(l, g, o)), onCloseMiniLayout: (miniLayoutId) => mutate((l) => ops.closeMiniLayout(l, miniLayoutId)), onResize: (sid, idx, delta, total) => mutate((l) => ops.resizeSplit(l, sid, idx, delta, total)), renderPanel, renderMiniLayout }} />
        ))}
      </div>
    </div>
  ), [mutate, renderPanel, windowId, t]);

  const handleMenuAction = useCallback((action: string) => {
    // Actions de layout (SPEC §6.2)
    if (action.startsWith('panel:add:')) {
      const panelId = action.slice('panel:add:'.length);
      const doAdd = () => mutate((l) => {
        // Ajoute au premier groupe si possible, sinon enracine.
        const groups = listGroups(l.tree);
        const target = groups[0]?.id ?? null;
        return ops.addPanel(l, target, panelId);
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
    } else if (action.startsWith('mini-layout:add:')) {
      const panelId = action.slice('mini-layout:add:'.length);
      mutate((l) => {
        const groups = listGroups(l.tree);
        const target = groups[0]?.id ?? null;
        return ops.addMiniLayout(l, target ?? '', panelId);
      });
    } else if (action.startsWith('panel:activate:')) {
      const occId = action.slice('panel:activate:'.length);
      mutate((l) => { const g = findGroup(l.tree, occId); return g ? ops.activateTab(l, g.id, occId) : l; });
    } else if (action === 'layout:save') {
      persistLayout(layout, { post: daemonPost });
      saveCurrentWindow(windowId, layoutNameFor(windowId)).catch(() => {});
    } else if (action === 'app:quit') {
      closeCurrentWindow(windowId).catch(() => {});
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
    } else if (action.startsWith('theme:set:')) {
      const themeName = action.slice('theme:set:'.length);
      applyTheme(themeName).then(() => {
        // Persiste le thème dans le layout + profil de fenêtre.
        mutate((l) => ({ ...l, theme: { ...(l.theme ?? {}), global: themeName } }));
      }).catch(() => {});
    } else if (action.startsWith('lang:set:')) {
      const loc = action.slice('lang:set:'.length);
      getLocaleAndApply(loc);
    } else if (action === 'window:fullscreen') {
      toggleFullscreen().catch(() => {});
    } else if (action === 'window:save') {
      persistLayout(layout, { post: daemonPost });
      saveCurrentWindow(windowId, layoutNameFor(windowId)).catch(() => {});
    }
    // Autres actions (theme:set…) : MVP.
  }, [mutate, layout, windowId]);

  const menuItems = resolved.menu;

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--mw-bg, #0f172a)', color: 'var(--mw-fg, #e2e8f0)', fontFamily: 'var(--mw-font-ui, sans-serif)', overflow: 'hidden' }}>
      <MenuBar items={menuItems} t={t} onAction={handleMenuAction} />
      {loadErr && (
        <div data-testid="mw-load-err" style={{ background: '#7f1d1d', color: '#fecaca', padding: '2px 10px', fontSize: 11 }}>
          {loadErr}
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, padding: 2 }}>
        {resolved.root ? (
          <SplitTree
            node={resolved.root}
            ctx={{
              windowId,
              t,
              onActivate: (g, o) => mutate((l) => ops.activateTab(l, g, o)),
              onClose: (g, o) => mutate((l) => ops.closeTab(l, g, o)),
              onMove: (from, to, o, idx) => mutate((l) => ops.moveTab(l, from, to, o, idx)),
              onSplit: (g, dir, o, from) => mutate((l) => ops.splitGroup(l, g, dir, o, from)),
              onExtract: (g, o) => mutate((l) => ops.closeTab(l, g, o)),
              onCloseMiniLayout: (miniLayoutId) => mutate((l) => ops.closeMiniLayout(l, miniLayoutId)),
              onResize: (sid, idx, delta, total) => mutate((l) => ops.resizeSplit(l, sid, idx, delta, total)),
              renderPanel,
              renderMiniLayout,
            }}
          />
        ) : (
          <div style={{ padding: 20, color: '#64748b', fontSize: 13 }}>
            Fenêtre vide — utilisez le menu pour ajouter des panneaux.
          </div>
        )}
      </div>
    </div>
  );
}
