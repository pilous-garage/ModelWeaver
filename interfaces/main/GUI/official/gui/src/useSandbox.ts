import { useState, useEffect, useCallback, useRef } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { parse as yamlParse, stringify as yamlStringify } from 'yaml';

export type CatalogueType = 'skills' | 'behaviors' | 'personalities' | 'roles' | 'agents';

export interface CatalogueItem {
  name: string;
  description?: string;
  [key: string]: any;
}

export interface OpenDoc {
  id: string;            // `${type}:${name}` (sauvé) ou `new:${type}:${n}` (brouillon)
  type: CatalogueType;
  name: string;          // '' tant que non enregistré
  title: string;         // libellé onglet
  text: string;          // YAML éditeur (mode normal)
  inlineText: string;    // YAML inline (agents)
  showInline: boolean;
  dirty: boolean;
  isNew: boolean;
}

const LS_KEY = 'mw_sandbox_docs';

export async function daemonPost(route: string, body: any): Promise<any> {
  return invoke<any>('daemon_post', { route, body: JSON.stringify(body) });
}

const listRoute: Record<CatalogueType, string> = {
  skills: 'catalogue/skills/list',
  behaviors: 'catalogue/behaviors/list',
  personalities: 'catalogue/personalities/list',
  roles: 'catalogue/roles/list',
  agents: 'catalogue/agents/list',
};
const getRoute: Record<CatalogueType, string> = {
  skills: 'catalogue/skills/get',
  behaviors: 'catalogue/behaviors/get',
  personalities: 'catalogue/personalities/get',
  roles: 'catalogue/roles/get',
  agents: 'catalogue/agents/get',
};
const saveRoute: Record<CatalogueType, string> = {
  skills: 'catalogue/skills/save',
  behaviors: 'catalogue/behaviors/save',
  personalities: 'catalogue/personalities/save',
  roles: 'catalogue/roles/save',
  agents: 'catalogue/agents/save',
};
const deleteRoute: Record<CatalogueType, string> = {
  skills: 'catalogue/skills/delete',
  behaviors: 'catalogue/behaviors/delete',
  personalities: 'catalogue/personalities/delete',
  roles: 'catalogue/roles/delete',
  agents: 'catalogue/agents/delete',
};

const TEMPLATES: Record<CatalogueType, string> = {
  skills: 'name: \ncategory: system\ndescription: ""\ninputs: {}\noutputs: {}\nimplementation:\n  type: python\n  code: |\n    def run(inputs, ws):\n        return {}\n',
  behaviors: 'name: \ndescription: ""\nworkflow:\n  steps:\n    - id: start\n      type: llm_call\n      request: ""\n      next: end\n    - id: end\n      type: end\n      status: SUCCESS\n',
  personalities: 'name: \ndescription: ""\ntone: neutre\nsystem_prompt: ""\n',
  roles: 'name: \nclass: worker\nsub_class: general\ndescription: ""\n',
  agents: 'name: \nrole: \npersonality:\n  tone: neutre\n  system_prompt: ""\nskills: []\nworkflow:\n  steps:\n    - id: start\n      type: llm_call\n      request: ""\n      next: end\n    - id: end\n      type: end\n      status: SUCCESS\n',
};

function extractName(yamlText: string): string | null {
  const m = yamlText.match(/^name:\s*(.+)$/m);
  if (m) return m[1].trim().replace(/^["']|["']$/g, '');
  return null;
}

function docId(type: CatalogueType, name: string): string {
  return `${type}:${name}`;
}

export function useSandbox() {
  // ── Catalogue (navigation) ──
  const [activeTab, setActiveTab] = useState<CatalogueType>('skills');
  const [items, setItems] = useState<CatalogueItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string>('');
  const [showCatalogue, setShowCatalogue] = useState(true);
  const [showLego, setShowLego] = useState(false);

  // ── Documents ouverts (onglets) ──
  const [openDocs, setOpenDocs] = useState<OpenDoc[]>([]);
  const [activeDocId, setActiveDocId] = useState<string | null>(null);
  const newCounter = useRef(1);
  const restored = useRef(false);

  const activeDoc = openDocs.find(d => d.id === activeDocId) || null;

  // ── Persistance localStorage ──
  useEffect(() => {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (raw) {
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed?.docs)) {
          setOpenDocs(parsed.docs);
          setActiveDocId(parsed.activeDocId ?? parsed.docs[0]?.id ?? null);
          const maxNew = parsed.docs
            .filter((d: OpenDoc) => d.isNew)
            .map((d: OpenDoc) => parseInt(d.id.split(':').pop() || '0', 10))
            .reduce((a: number, b: number) => Math.max(a, b), 0);
          newCounter.current = maxNew + 1;
        }
      }
    } catch { /* ignore */ }
    restored.current = true;
  }, []);

  useEffect(() => {
    if (!restored.current) return;
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ docs: openDocs, activeDocId }));
    } catch { /* ignore */ }
  }, [openDocs, activeDocId]);

  // ── Catalogue : liste ──
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await daemonPost(listRoute[activeTab], {});
      if (res?.ok && res?.result) {
        setItems(res.result[activeTab] || []);
      } else {
        setItems([]);
      }
    } catch (e: any) {
      setError(`Échec liste ${activeTab}: ${e}`);
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => { refresh(); }, [activeTab, refresh]);

  const patchDoc = useCallback((id: string, patch: Partial<OpenDoc>) => {
    setOpenDocs(docs => docs.map(d => (d.id === id ? { ...d, ...patch } : d)));
  }, []);

  // ── Ouvrir un item du catalogue dans un onglet ──
  const openItem = useCallback(async (name: string) => {
    const id = docId(activeTab, name);
    const existing = openDocs.find(d => d.id === id);
    if (existing) { setActiveDocId(id); return; }
    setMsg('');
    try {
      const res = await daemonPost(getRoute[activeTab], { name });
      const yaml = res?.ok && res?.result ? (res.result.yaml || '') : '';
      const inline = res?.ok && res?.result ? (res.result.inline_yaml || '') : '';
      const doc: OpenDoc = {
        id, type: activeTab, name, title: name,
        text: yaml, inlineText: inline, showInline: false, dirty: false, isNew: false,
      };
      setOpenDocs(docs => [...docs, doc]);
      setActiveDocId(id);
    } catch (e: any) {
      setError(`Échec chargement ${name}: ${e}`);
    }
  }, [activeTab, openDocs]);

  // ── Nouveau brouillon ──
  const newDoc = useCallback((type?: CatalogueType) => {
    const t = type || activeTab;
    const n = newCounter.current++;
    const id = `new:${t}:${n}`;
    const doc: OpenDoc = {
      id, type: t, name: '', title: `Nouveau ${t} ${n}`,
      text: TEMPLATES[t], inlineText: '', showInline: false, dirty: true, isNew: true,
    };
    setOpenDocs(docs => [...docs, doc]);
    setActiveDocId(id);
  }, [activeTab]);

  const activateDoc = useCallback((id: string) => setActiveDocId(id), []);

  const closeDoc = useCallback((id: string) => {
    const doc = openDocs.find(d => d.id === id);
    if (doc?.dirty) {
      const ok = window.confirm(`« ${doc.title} » a des modifications non enregistrées. Fermer quand même ?`);
      if (!ok) return;
    }
    setOpenDocs(docs => {
      const idx = docs.findIndex(d => d.id === id);
      const next = docs.filter(d => d.id !== id);
      if (activeDocId === id) {
        const fallback = next[idx] || next[idx - 1] || next[0] || null;
        setActiveDocId(fallback ? fallback.id : null);
      }
      return next;
    });
  }, [openDocs, activeDocId]);

  const setDocText = useCallback((id: string, text: string) => {
    patchDoc(id, { text, dirty: true });
  }, [patchDoc]);

  const setDocInline = useCallback((id: string, inlineText: string) => {
    patchDoc(id, { inlineText });
  }, [patchDoc]);

  const toggleInline = useCallback((id: string) => {
    setOpenDocs(docs => docs.map(d => (d.id === id ? { ...d, showInline: !d.showInline } : d)));
  }, []);

  // ── Enregistrer un document ──
  const saveDoc = useCallback(async (id: string) => {
    const doc = openDocs.find(d => d.id === id);
    if (!doc) return;
    setMsg('');
    const name = doc.name || extractName(doc.text);
    if (!name) {
      setError('Impossible de déduire le nom (champ `name:` manquant)');
      return;
    }
    try {
      const res = await daemonPost(saveRoute[doc.type], { name, yaml: doc.text });
      if (res?.ok && res?.result?.status === 'ok') {
        const newId = docId(doc.type, name);
        let inline = doc.inlineText;
        if (doc.type === 'agents') {
          const inl = await daemonPost('catalogue/agents/inline', { name });
          if (inl?.ok && inl?.result) inline = inl.result.inline_yaml || '';
        }
        setOpenDocs(docs => docs.map(d => (d.id === id
          ? { ...d, id: newId, name, title: name, dirty: false, isNew: false, inlineText: inline }
          : d)));
        if (activeDocId === id) setActiveDocId(newId);
        setMsg('✅ Enregistré : ' + name);
        if (doc.type === activeTab) await refresh();
      } else {
        setError('Échec save : ' + (res?.result?.error || res?.error || 'inconnu'));
      }
    } catch (e: any) {
      setError(`Échec save: ${e}`);
    }
  }, [openDocs, activeDocId, activeTab, refresh]);

  const saveActive = useCallback(() => {
    if (activeDocId) saveDoc(activeDocId);
  }, [activeDocId, saveDoc]);

  const saveAll = useCallback(async () => {
    for (const d of openDocs) {
      if (d.dirty) await saveDoc(d.id);
    }
  }, [openDocs, saveDoc]);

  // ── Générer inline (agents) ──
  const generateInline = useCallback(async (id: string) => {
    const doc = openDocs.find(d => d.id === id);
    if (!doc || !doc.name) {
      setError("Enregistrez l'agent d'abord");
      return;
    }
    setMsg('');
    try {
      const res = await daemonPost('catalogue/agents/inline', { name: doc.name });
      if (res?.ok && res?.result) {
        patchDoc(id, { inlineText: res.result.inline_yaml || '', showInline: true });
        setMsg('✅ Inline généré');
      } else {
        setError('Échec inline : ' + (res?.result?.error || res?.error || 'inconnu'));
      }
    } catch (e: any) {
      setError(`Échec inline: ${e}`);
    }
  }, [openDocs, patchDoc]);

  // ── Supprimer un item du catalogue ──
  const deleteItem = useCallback(async (name: string) => {
    setMsg('');
    try {
      const res = await daemonPost(deleteRoute[activeTab], { name });
      if (res?.ok && res?.result?.status === 'ok') {
        setMsg('🗑️ Supprimé : ' + name);
        const id = docId(activeTab, name);
        setOpenDocs(docs => docs.filter(d => d.id !== id));
        if (activeDocId === id) setActiveDocId(null);
        await refresh();
      } else {
        setError('Échec delete : ' + (res?.result?.error || res?.error || 'inconnu'));
      }
    } catch (e: any) {
      setError(`Échec delete: ${e}`);
    }
  }, [activeTab, activeDocId, refresh]);

  // ── Pont lego ↔ YAML (document actif) ──
  const patchActiveDoc = useCallback((mutator: (obj: any) => void) => {
    setOpenDocs(docs => docs.map(d => {
      if (d.id !== activeDocId) return d;
      let obj: any;
      try { obj = yamlParse(d.text) || {}; } catch { return d; }
      if (typeof obj !== 'object' || obj === null) obj = {};
      mutator(obj);
      let text = d.text;
      try { text = yamlStringify(obj); } catch { return d; }
      return { ...d, text, dirty: true };
    }));
  }, [activeDocId]);

  const fetchItemParsed = useCallback(async (type: CatalogueType, name: string): Promise<any | null> => {
    try {
      const res = await daemonPost(getRoute[type], { name });
      if (res?.ok && res?.result?.yaml) return yamlParse(res.result.yaml) || {};
    } catch { /* ignore */ }
    return null;
  }, []);

  const rescanLib = useCallback(async () => {
    setMsg('');
    try {
      const res = await daemonPost('lib/scan', {});
      const n = res?.result?.count;
      setMsg(`🔄 Librairie rescannée${n != null ? ` (${n} fonctions)` : ''}`);
    } catch (e: any) {
      setError(`Échec rescan lib: ${e}`);
    }
  }, []);

  return {
    // catalogue
    activeTab, setActiveTab, items, loading, error, msg, setError, setMsg,
    showCatalogue, setShowCatalogue, refresh, deleteItem,
    showLego, setShowLego,
    // documents
    openDocs, activeDocId, activeDoc,
    openItem, newDoc, activateDoc, closeDoc,
    setDocText, setDocInline, toggleInline,
    saveDoc, saveActive, saveAll, generateInline,
    // lego ↔ yaml
    patchActiveDoc, fetchItemParsed,
    // outils
    rescanLib,
  };
}
