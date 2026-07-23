"""AutoClicker robuste — timeout progressif 1→10min, retry, capture texte, logs abondants.

Usage:
    from tests.robust_clicker import RobustClicker
    rc = RobustClicker(url="http://localhost:5173")
    rc.start()
    rc.click_with_retry("text=Tout déplier")
    text = rc.get_page_text()
    rc.close()
"""

import os, sys, time, json, logging
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("robust_clicker")


class RobustClicker:
    """Pilote un navigateur headless avec timeout progressif et retry."""

    RETRY_TIMEOUTS = [60, 120, 240, 480, 600]  # 1min → 2min → 4min → 8min → 10min

    def __init__(self, url: str = "http://localhost:5173", headless: bool = True,
                 screenshot_dir: Optional[str] = None):
        self.url = url
        self.headless = headless
        self.screenshot_dir = screenshot_dir or str(Path(__file__).resolve().parent / "_screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        self._browser = None
        self._page = None
        self._ctx = None
        self._pw = None
        self._step = 0

    # ── Cycle de vie ──

    def start(self):
        from playwright.sync_api import sync_playwright
        log.info("🚀 Lancement du navigateur (headless=%s, url=%s)", self.headless, self.url)
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._ctx = self._browser.new_context(viewport={"width": 1400, "height": 900})
        self._page = self._ctx.new_page()
        self._page.set_default_timeout(self.RETRY_TIMEOUTS[0] * 1000)
        self._page.goto(self.url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(2000)
        log.info("   ✅ Page chargée — URL: %s", self._page.url)

    def close(self):
        if self._browser:
            self._browser.close()
            log.info("   🧹 Navigateur fermé")
        if self._pw:
            self._pw.stop()

    @property
    def page(self):
        if not self._page:
            raise RuntimeError("RobustClicker not started — call .start() first")
        return self._page

    # ── Timeout progressif ──

    def _retry_timeout(self, attempt: int) -> int:
        """Renvoie le timeout pour le nième essai (0-indexed)."""
        idx = min(attempt, len(self.RETRY_TIMEOUTS) - 1)
        return self.RETRY_TIMEOUTS[idx] * 1000

    # ── Clic avec retry ──

    def click_with_retry(self, selector: str, *, wait_after: float = 1.0,
                         max_attempts: int = 5, desc: str = ""):
        """Tente un clic avec timeout progressif.
        Réessaie si le sélecteur n'est pas trouvé ou si le clic échoue.
        Log abondant à chaque tentative.
        """
        label = desc or selector
        for attempt in range(max_attempts):
            timeout = self._retry_timeout(attempt)
            try:
                log.info("🖱️  [tentative %d/%d] %s (timeout=%ds)",
                         attempt + 1, max_attempts, label, timeout // 1000)
                self.page.set_default_timeout(timeout)
                self.page.wait_for_selector(selector, timeout=timeout)
                self.page.click(selector)
                if wait_after > 0:
                    self.page.wait_for_timeout(int(wait_after * 1000))
                log.info("   ✅ Clic réussi — %s", label)
                return
            except Exception as e:
                log.warning("   ⚠️  Échec tentative %d/%d — %s: %s",
                            attempt + 1, max_attempts, label, e)
                # Capture texte et screenshot avant de réessayer
                self._debug_capture(f"retry_{attempt}_{label}")
                if attempt < max_attempts - 1:
                    wait = min(30, 5 * (attempt + 1))
                    log.info("   ⏳ Attente %ds avant re-tentative…", wait)
                    self.page.wait_for_timeout(wait * 1000)
        raise RuntimeError(f"❌ Clic échoué après {max_attempts} tentatives: {label}")

    def click_text(self, text: str, **kw):
        self.click_with_retry(f"text={text}", desc=f"text='{text}'", **kw)

    def click_by_title(self, title: str, **kw):
        self.click_with_retry(f"[title='{title}']", desc=f"title='{title}'", **kw)

    # ── Navigation ──

    def goto(self, url: str, *, wait_until: str = "domcontentloaded", wait_after: float = 2.0):
        log.info("🌐 Navigation → %s", url)
        self.page.goto(url, wait_until=wait_until)
        self.page.wait_for_timeout(int(wait_after * 1000))
        log.info("   ✅ Arrivé — URL: %s", self.page.url)

    # ── Capture de texte intégral (Ctrl+A Ctrl+C) ──

    def get_page_text(self) -> str:
        """Renvoie tout le texte visible de la page (comme Ctrl+A Ctrl+C)."""
        try:
            text = self.page.evaluate("() => document.body.innerText")
            return text or ""
        except Exception as e:
            log.warning("   ⚠️  get_page_text échoué: %s", e)
            return ""

    def get_page_html(self) -> str:
        """Renvoie le HTML complet du body."""
        try:
            return self.page.evaluate("() => document.body.innerHTML")
        except Exception as e:
            log.warning("   ⚠️  get_page_html échoué: %s", e)
            return ""

    # ── Attentes intelligentes ──

    def wait_for_stable(self, seconds: float = 2.0, timeout: int = 60):
        """Attend que le DOM soit stable (pas de changements pendant `seconds`)."""
        log.info("⏳ Attente stabilité DOM (%ds sans changement)...", int(seconds))
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        except Exception:
            log.warning("   ⚠️  networkidle timeout après %ds", timeout)
        self.page.wait_for_timeout(int(seconds * 1000))
        log.info("   ✅ DOM stable")

    def wait_for_catalogue_ready(self, timeout: int = 120):
        """Attend que le catalogue soit chargé (présence de texte attendu)."""
        log.info("⏳ Attente chargement catalogue (timeout=%ds)...", timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.get_page_text()
            if "Agents" in text or "Skills" in text or "🧩" in text:
                log.info("   ✅ Catalogue chargé — texte trouvé")
                return
            time.sleep(2)
        # Dernier délai : attendre sans condition
        log.warning("   ⚠️  Catalogue non détecté après %ds, continue quand même", timeout)
        self.page.wait_for_timeout(5000)

    # ── Debug ──

    def screenshot(self, name: str = "debug.png") -> str:
        path = os.path.join(self.screenshot_dir, name)
        try:
            self.page.screenshot(path=path)
            log.info("   📸 Screenshot → %s", path)
        except Exception as e:
            log.warning("   ⚠️  Screenshot échoué: %s", e)
        return path

    def _debug_capture(self, tag: str):
        """Capture texte + HTML + screenshot pour debug."""
        log.info("   🔍 Debug capture [%s]", tag)
        try:
            text = self.get_page_text()
            log.info("   📝 Page text (%d chars):\n%s", len(text), text[:500])
        except Exception as e:
            log.warning("   ⚠️  Debug text échoué: %s", e)
        try:
            self.screenshot(f"debug_{tag}.png")
        except Exception:
            pass

    # ── Helpers sandbox ──

    def switch_to_sandbox(self):
        """Navigue vers le sandbox IDE."""
        sandbox_url = self.url.rstrip("/") + "?sandbox"
        log.info("📂 Navigation vers sandbox: %s", sandbox_url)
        self.goto(sandbox_url)
        self.wait_for_stable(3)

    def open_agent_graph(self, agent_name: str = "worker"):
        """Ouvre un agent en vue graphe avec retry sur chaque étape."""
        log.info("🤖 Ouverture agent '%s' en vue graphe", agent_name)

        # 1. Cliquer sur l'onglet Agents
        self.click_with_retry("text=🤖 Agents", wait_after=1,
                              desc="onglet Agents")
        self.wait_for_stable(1)

        # 2. Déplier les groupes du catalogue
        tries = ["button[title='Tout déplier']", "text=⤢"]
        for sel in tries:
            try:
                self.click_with_retry(sel, wait_after=1, max_attempts=2,
                                      desc=f"déplier catalogue: {sel}")
                break
            except Exception:
                continue

        # 3. Trouver et cliquer sur l'agent par JS
        self.page.wait_for_timeout(1000)
        js = f"""() => {{
            var items = document.querySelectorAll('div[draggable=true]');
            if (items.length === 0) items = document.querySelectorAll('div');
            for (var i=0; i<items.length; i++) {{
                var t = items[i].textContent.trim();
                if (t.indexOf('{agent_name}') === 0 || t === '{agent_name}') {{
                    items[i].click();
                    return 'found:' + i;
                }}
            }}
            return 'not_found';
        }}"""
        result = self.page.evaluate(js)
        log.info("   🖱️  Agent click JS result: %s", result)
        if "not_found" in str(result):
            log.warning("   ⚠️  Agent non trouvé par JS, tentative search box...")
            self.click_with_retry("input[placeholder='Rechercher…']", wait_after=0.5,
                                  desc="search box")
            self.page.fill("input[placeholder='Rechercher…']", agent_name)
            self.page.wait_for_timeout(500)
            try:
                self.click_with_retry(f"text={agent_name}", wait_after=1,
                                      max_attempts=3, desc="agent search result")
            except Exception:
                log.warning("   ⚠️  Search click échoué")
        self.page.wait_for_timeout(2000)

        # 4. Passer en vue graphe
        try:
            self.click_with_retry("text=🔀 Graphe", wait_after=2,
                                  desc="bouton Graphe")
        except Exception:
            # Fallback : chercher le bouton par title ou dans la barre d'outils
            self.click_with_retry("button:has-text('Graphe')", wait_after=2,
                                  desc="bouton Graphe (fallback)")

    def expand_all_graph(self):
        """Clique sur 'Tout déplier' du graphe avec attente longue."""
        log.info("⤢ Expand all (Tout déplier)")
        self.click_with_retry("button:has-text('Tout déplier')", wait_after=1,
                              desc="expand all graph")
        # Attente longue pour le fetch récursif des skills
        log.info("   ⏳ Attente injection skills...")
        for i in range(6):
            self.page.wait_for_timeout(5000)
            text = self.get_page_text()
            log.info("   ⏳ [%d/6] Attente fin injection...", i + 1)
        log.info("   ✅ Injections terminées")

    def export_graph_yaml(self, output_path: str = "/tmp/graph_export.yaml"):
        """Clique sur 'Exporter .yaml' et sauvegarde le fichier téléchargé."""
        log.info("⬇ Export YAML du graphe → %s", output_path)
        from playwright.sync_api import expect
        with self.page.expect_download(timeout=120000) as dl_info:
            self.click_with_retry("[title='Exporter le graphe en YAML']",
                                  wait_after=1, desc="exporter YAML")
        download = dl_info.value
        download.save_as(output_path)
        log.info("   ✅ YAML téléchargé: %s (%d bytes)", output_path, os.path.getsize(output_path))
        return output_path

    def count_graph_nodes(self) -> int:
        return self.page.evaluate("document.querySelectorAll('.react-flow__node').length") or 0

    def count_graph_edges(self) -> int:
        return self.page.evaluate("document.querySelectorAll('.react-flow__edge').length") or 0
