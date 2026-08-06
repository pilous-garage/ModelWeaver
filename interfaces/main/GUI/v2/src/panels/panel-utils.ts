// panel-utils.ts — helpers partagés des panels migrés.
// Polling avec garde de vie + unwrap de réponse daemon.

import { useEffect, useRef, useState } from 'react';
import type { PanelDef } from './contract.ts';

/** Poll un POST daemon régulièrement (immediate + interval). */
export function usePoll<T>(post: (route: string, body?: any) => Promise<any>, route: string, body: any, intervalMs: number, unwrap: (res: any) => T, withReload = false): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const tick = async () => {
      try {
        const res = await post(route, body);
        if (alive.current) { setData(unwrap(res)); setError(null); }
      } catch (e: any) {
        if (alive.current) setError(String(e?.message ?? e));
      }
    };
    tick();
    const iv = setInterval(tick, intervalMs);
    return () => { alive.current = false; clearInterval(iv); };
    // body stable via JSON (les objets inline {}) — ne pas re-créer le poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [post, route, intervalMs, nonce, JSON.stringify(body ?? {})]);

  const reload = () => setNonce((n) => n + 1);

  if (withReload) return { data, error, reload };
  return { data, error, reload };
}

/** Unwrap standard daemon : {result: ...} sinon la réponse brute. */
export function unwrapResult(res: any): any {
  return res?.result ?? res ?? {};
}

/** Déclaration standard d'un panel migré. */
export function makeDeclaration(id: string, label: string, version: string, routes: string[]): () => string {
  return () => `[${id}] ${label} v${version}\n  routes: ${routes.join(', ')}`;
}