// Positions manuelles des nœuds de graphe — purement esthétique.
// Stockées à part (localStorage), JAMAIS écrites dans le YAML/inline.
const LS_KEY = 'mw_sandbox_graph_layout';

type Pos = { x: number; y: number };
type Store = Record<string, Record<string, Pos>>; // docId → stepId → pos

function readStore(): Store {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) return JSON.parse(raw) as Store;
  } catch { /* ignore */ }
  return {};
}

function writeStore(store: Store) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(store)); } catch { /* ignore */ }
}

export function loadPositions(docId: string): Record<string, Pos> {
  return readStore()[docId] || {};
}

export function savePositions(docId: string, positions: Record<string, Pos>) {
  const store = readStore();
  store[docId] = positions;
  writeStore(store);
}

export function clearPositions(docId: string) {
  const store = readStore();
  delete store[docId];
  writeStore(store);
}
