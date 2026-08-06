// Tests du système i18n (fr/en) : résolution, bascule, persistance.

import { describe, it, expect } from 'vitest';
import { t, setLocale, getLocale, loadSavedLocale, persistLocale, loadLangYaml } from './i18n.ts';

describe('i18n', () => {
  it('résout une clé en français par défaut', () => {
    setLocale('fr');
    expect(t('menu.fichier')).toBe('Fichier');
    expect(t('menu.langue')).toBe('Langue');
  });

  it('bascule en anglais', () => {
    setLocale('en');
    expect(getLocale()).toBe('en');
    expect(t('menu.fichier')).toBe('File');
    expect(t('menu.langue')).toBe('Language');
    setLocale('fr');
  });

  it('retourne la clé si introuvable', () => {
    expect(t('clé.inexistante')).toBe('clé.inexistante');
  });

  it('charge un YAML supplémentaire (fusion)', () => {
    setLocale('fr');
    loadLangYaml('panels:\n  test:\n    titre: "Mon panel"');
    expect(t('panels.test.titre')).toBe('Mon panel');
  });

  it('persiste et recharge la langue (via mock daemon)', async () => {
    const posts: { route: string; body: any }[] = [];
    const mockPost = async (route: string, body: any) => {
      posts.push({ route, body });
      return { ok: true };
    };
    await persistLocale('en', mockPost);
    expect(posts.length).toBe(1);
    // rechargement : on simule la lecture d'un daemon qui a stocké la préférence
    const stored = new Map(posts.map((p) => [p.route, p.body]));
    const readPost = async (route: string) => {
      if (route === 'session/get') return { result: { yaml: stored.get('session/save')?.yaml ?? '' } };
      return { ok: true };
    };
    setLocale('fr');
    const loc = await loadSavedLocale(readPost);
    expect(loc).toBe('en');
    setLocale('fr');
  });
});
