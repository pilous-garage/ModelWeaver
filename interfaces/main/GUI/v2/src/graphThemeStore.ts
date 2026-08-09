// graphThemeStore.ts — état GLOBAL du thème graphe (partagé par les panels
// de graphe + le menu « Affichage → Thème graphe »).
//
// Le thème graphe courant est choisi par l'utilisateur dans le menu ; chaque
// GrapheSubPanel le lit via le hook. La liste vient du daemon (theme/list
// kind=graphe), le contenu via theme/get kind=graphe.

import { useCallback, useSyncExternalStore } from 'react';
import { parse as parseYaml } from 'yaml';

export interface GraphThemeInfo {
  name: string;
  default: boolean;
}

interface GraphThemeState {
  list: string[];
  current: string;
  obj: any;             // thème YAML parsé (ou null)
  objName: string;
}

let _state: GraphThemeState = { list: [], current: 'basique', obj: null, objName: '' };
const _listeners = new Set<() => void>();

function emit() {
  for (const l of _listeners) l();
}

function _get() {
  return _state;
}

export function getGraphTheme(): GraphThemeState {
  return _state;
}

export function setGraphThemeName(name: string): void {
  if (_state.current === name) return;
  _state = { ..._state, current: name, objName: name, obj: null };
  emit();
}

/** Charge la liste des thèmes graphe (daemon) au boot. */
export function loadGraphThemeList(apiPost: (route: string, body?: any) => Promise<any>): void {
  apiPost('theme/list', { kind: 'graphe' })
    .then((res: any) => {
      const r = res?.result ?? res ?? {};
      const names = (r.themes ?? []).map((t: any) => t.name);
      if (names.length) {
        _state = { ..._state, list: names };
        emit();
      }
    })
    .catch(() => {});
}

/** Charge le contenu du thème courant (daemon). */
export function loadGraphThemeObj(apiPost: (route: string, body?: any) => Promise<any>): void {
  const name = _state.current;
  if (!name) return;
  apiPost('theme/get', { name, kind: 'graphe' })
    .then((res: any) => {
      const r = res?.result ?? res ?? {};
      if (r.yaml) {
        try {
          _state = { ..._state, obj: parseYaml(r.yaml), objName: name };
        } catch {
          _state = { ..._state, obj: null, objName: name };
        }
        emit();
      }
    })
    .catch(() => {});
}

/** Hook React : abonnement au thème graphe courant. */
export function useGraphTheme(): GraphThemeState {
  const state = useSyncExternalStore(
    (cb) => { _listeners.add(cb); return () => { _listeners.delete(cb); }; },
    _get,
    _get,
  );
  return state;
}

/** Hook : contrôleur (setCurrent + init). `apiPost` pour le chargement daemon. */
export function useGraphThemeControl(apiPost: (route: string, body?: any) => Promise<any>): {
  list: string[];
  current: string;
  obj: any;
  setCurrent: (name: string) => void;
  ensureLoaded: (apiPost2?: (route: string, body?: any) => Promise<any>) => void;
} {
  const state = useGraphTheme();
  const setCurrent = useCallback((name: string) => {
    setGraphThemeName(name);
    if (apiPost) loadGraphThemeObj(apiPost);
  }, [apiPost]);
  const ensureLoaded = useCallback((apiPost2?: (route: string, body?: any) => Promise<any>) => {
    const post = apiPost2 ?? apiPost;
    if (!post) return;
    if (!_state.list.length) loadGraphThemeList(post);
    if (!_state.obj || _state.objName !== _state.current) loadGraphThemeObj(post);
  }, [apiPost]);
  return {
    list: state.list,
    current: state.current,
    obj: state.obj,
    setCurrent,
    ensureLoaded,
  };
}
