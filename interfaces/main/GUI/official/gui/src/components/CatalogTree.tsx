import React, { useMemo, useState, useCallback } from 'react';
import { fuzzyScore } from '../lib/fuzzy.ts';

// Module commun : liste repliable par classe/sous-classe (profondeur arbitraire),
// barre de recherche floue, et boutons Tout déplier / Tout replier.
//
// Le consommateur fournit :
//  - items          : la table à afficher
//  - getKey         : clé unique par ligne
//  - getGroupPath   : hiérarchie de repli, ex. ['system', 'file'] (peut être [] = à plat)
//  - getSearchText  : texte utilisé pour la recherche floue
//  - renderItem     : rendu d'une ligne (contrôle total : drag, boutons, etc.)

export type CatalogTheme = 'slate' | 'catppuccin';

interface Colors {
  headerBg: string;
  headerHoverBg: string;
  border: string;
  text: string;
  muted: string;
  inputBg: string;
  accent: string;
}

const THEMES: Record<CatalogTheme, Colors> = {
  slate: {
    headerBg: '#0f172a', headerHoverBg: '#162033', border: '#334155',
    text: '#e2e8f0', muted: '#64748b', inputBg: '#0f172a', accent: '#3b82f6',
  },
  catppuccin: {
    headerBg: '#181825', headerHoverBg: '#313244', border: '#45475a',
    text: '#cdd6f4', muted: '#6c7086', inputBg: '#181825', accent: '#89b4fa',
  },
};

interface Props<T> {
  items: T[];
  getKey: (item: T) => string;
  getGroupPath: (item: T) => string[];
  getSearchText: (item: T) => string;
  renderItem: (item: T) => React.ReactNode;
  theme?: CatalogTheme;
  storageKey?: string;          // persistance de l'état replié
  searchPlaceholder?: string;
  emptyText?: string;
  groupSort?: (a: string, b: string) => number;
  itemGap?: number;
  toolbarExtra?: React.ReactNode;
}

interface TreeNode<T> {
  name: string;
  path: string;
  depth: number;
  children: Map<string, TreeNode<T>>;
  items: { item: T; score: number }[];
}

function newNode<T>(name: string, path: string, depth: number): TreeNode<T> {
  return { name, path, depth, children: new Map(), items: [] };
}

function loadCollapsed(storageKey?: string): Set<string> {
  if (!storageKey) return new Set();
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw) return new Set(JSON.parse(raw) as string[]);
  } catch { /* ignore */ }
  return new Set();
}

export function CatalogTree<T>(props: Props<T>) {
  const {
    items, getKey, getGroupPath, getSearchText, renderItem,
    theme = 'slate', storageKey, searchPlaceholder = 'Rechercher…',
    emptyText = 'Aucun élément.', groupSort, itemGap = 6, toolbarExtra,
  } = props;
  const c = THEMES[theme];

  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(() => loadCollapsed(storageKey));

  const persist = useCallback((next: Set<string>) => {
    if (storageKey) {
      try { localStorage.setItem(storageKey, JSON.stringify([...next])); } catch { /* ignore */ }
    }
  }, [storageKey]);

  const searching = query.trim().length > 0;

  // Construit l'arbre (filtré + scoré si recherche active).
  const { root, allPaths } = useMemo(() => {
    const r = newNode<T>('', '', -1);
    const paths = new Set<string>();
    for (const item of items) {
      const score = searching ? fuzzyScore(query, getSearchText(item)) : 0;
      if (score == null) continue;
      const path = getGroupPath(item).filter(Boolean);
      let node = r;
      let acc = '';
      for (let d = 0; d < path.length; d++) {
        acc = acc ? `${acc}/${path[d]}` : path[d];
        paths.add(acc);
        let child = node.children.get(path[d]);
        if (!child) { child = newNode<T>(path[d], acc, d); node.children.set(path[d], child); }
        node = child;
      }
      node.items.push({ item, score: score ?? 0 });
    }
    return { root: r, allPaths: paths };
  }, [items, query, searching, getGroupPath, getSearchText]);

  const cmp = groupSort || ((a: string, b: string) => a.localeCompare(b));

  const countOf = useCallback((n: TreeNode<T>): number => {
    let total = n.items.length;
    for (const ch of n.children.values()) total += countOf(ch);
    return total;
  }, []);

  const bestScore = useCallback((n: TreeNode<T>): number => {
    let best = -Infinity;
    for (const it of n.items) best = Math.max(best, it.score);
    for (const ch of n.children.values()) best = Math.max(best, bestScore(ch));
    return best;
  }, []);

  const toggle = (path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      persist(next);
      return next;
    });
  };
  const expandAll = () => { const next = new Set<string>(); setCollapsed(next); persist(next); };
  const collapseAll = () => { const next = new Set(allPaths); setCollapsed(next); persist(next); };

  const renderNode = (n: TreeNode<T>): React.ReactNode => {
    const childKeys = [...n.children.keys()].sort((a, b) => {
      if (searching) return bestScore(n.children.get(b)!) - bestScore(n.children.get(a)!);
      return cmp(a, b);
    });
    const sortedItems = searching
      ? [...n.items].sort((a, b) => b.score - a.score)
      : [...n.items].sort((a, b) => getSearchText(a.item).localeCompare(getSearchText(b.item)));

    return (
      <>
        {childKeys.map((key) => {
          const child = n.children.get(key)!;
          const isCollapsed = !searching && collapsed.has(child.path);
          return (
            <div key={child.path}>
              <button
                onClick={() => toggle(child.path)}
                style={{
                  width: '100%', textAlign: 'left', backgroundColor: c.headerBg, color: c.text,
                  border: `1px solid ${c.border}`, borderRadius: 6, padding: '5px 8px',
                  fontSize: 12, fontWeight: 600, cursor: 'pointer', display: 'flex',
                  justifyContent: 'space-between', alignItems: 'center', marginLeft: child.depth * 12,
                }}
                onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = c.headerHoverBg)}
                onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = c.headerBg)}
              >
                <span>{isCollapsed ? '▸' : '▾'} {child.name}</span>
                <span style={{ color: c.muted, fontWeight: 400 }}>{countOf(child)}</span>
              </button>
              {!isCollapsed && (
                <div style={{ marginTop: itemGap, display: 'flex', flexDirection: 'column', gap: itemGap, marginLeft: (child.depth + 1) * 12 }}>
                  {renderNode(child)}
                </div>
              )}
            </div>
          );
        })}
        {sortedItems.map(({ item }) => (
          <div key={getKey(item)}>{renderItem(item)}</div>
        ))}
      </>
    );
  };

  const total = countOf(root);
  const btn: React.CSSProperties = {
    background: 'transparent', color: c.muted, border: `1px solid ${c.border}`,
    borderRadius: 5, padding: '2px 7px', fontSize: 10.5, cursor: 'pointer',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, minHeight: 0, flex: 1 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <div style={{ position: 'relative', flex: 1 }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={searchPlaceholder}
            style={{
              width: '100%', boxSizing: 'border-box', backgroundColor: c.inputBg, color: c.text,
              border: `1px solid ${c.border}`, borderRadius: 6, padding: '5px 24px 5px 8px', fontSize: 12, outline: 'none',
            }}
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              style={{ position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)', background: 'transparent', border: 'none', color: c.muted, cursor: 'pointer', fontSize: 13 }}
              title="Effacer"
            >✕</button>
          )}
        </div>
        <button onClick={expandAll} style={btn} title="Tout déplier">⤢</button>
        <button onClick={collapseAll} style={btn} title="Tout replier">⤡</button>
        {toolbarExtra}
      </div>
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: itemGap }}>
        {total === 0 ? (
          <div style={{ color: c.muted, fontSize: 12, padding: 8 }}>
            {searching ? 'Aucun résultat.' : emptyText}
          </div>
        ) : renderNode(root)}
      </div>
    </div>
  );
}
