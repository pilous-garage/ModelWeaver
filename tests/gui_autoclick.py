"""Module de test GUI automatisé — autoclick pour Tauri/Vite.

Utilise Playwright pour piloter un navigateur headless.

Usage:
    from tests.gui_autoclick import AutoClicker

    ac = AutoClicker(url="http://localhost:5173")
    ac.click("text=Tout déplier")
    ac.wait(1)
    ac.screenshot("expand_result.png")
    ac.close()
"""

import os
import time
from pathlib import Path
from typing import Optional


class AutoClicker:
    """Pilote un navigateur headless via Playwright."""

    def __init__(self, url: str = "http://localhost:5173", headless: bool = True,
                 timeout: int = 30000, screenshot_dir: Optional[str] = None):
        self.url = url
        self.headless = headless
        self.timeout = timeout
        self.screenshot_dir = screenshot_dir or str(Path(__file__).resolve().parent / "_screenshots")
        os.makedirs(self.screenshot_dir, exist_ok=True)
        self._browser = None
        self._page = None
        self._ctx = None

    def start(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._ctx = self._browser.new_context(viewport={"width": 1400, "height": 900})
        self._page = self._ctx.new_page()
        self._page.set_default_timeout(self.timeout)
        self._page.goto(self.url)
        self._page.wait_for_load_state("networkidle")

    def close(self):
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    @property
    def page(self):
        if not self._page:
            raise RuntimeError("AutoClicker not started — call .start() first")
        return self._page

    def wait(self, seconds: float = 1):
        time.sleep(seconds)

    def click(self, selector: str, *, wait_before: float = 0.5, wait_after: float = 0.5):
        self.wait(wait_before)
        self.page.click(selector)
        self.wait(wait_after)

    def click_text(self, text: str, **kw):
        self.click(f"text={text}", **kw)

    def click_by_title(self, title: str, **kw):
        self.click(f"[title='{title}']", **kw)

    def fill(self, selector: str, value: str):
        self.page.fill(selector, value)

    def screenshot(self, name: str = "screenshot.png") -> str:
        path = os.path.join(self.screenshot_dir, name)
        self.page.screenshot(path=path)
        return path

    def html(self, selector: str = "body") -> str:
        return self.page.inner_html(selector)

    def text(self, selector: str = "body") -> str:
        return self.page.inner_text(selector)

    def eval(self, js: str):
        return self.page.evaluate(js)

    def log_console(self, filter_str: str = "expandAll"):
        """Récupère les logs console filtrés."""
        lines = []
        self.page.on("console", lambda msg: lines.append(msg.text) if filter_str in msg.text else None)
        return lines

    def wait_for_text(self, text: str, timeout: int = 10000):
        self.page.wait_for_selector(f"text={text}", timeout=timeout)

    def wait_for_selector(self, selector: str, timeout: int = 10000):
        self.page.wait_for_selector(selector, timeout=timeout)


class TauriAutoClicker(AutoClicker):
    """Variante pour Tauri — ajoute des helpers spécifiques."""

    def switch_to_sandbox(self):
        """Ouvre le panneau sandbox (suppose un bouton 'Sandbox')."""
        self.click_text("Sandbox")

    def open_agent_graph(self, agent_name: str = "worker"):
        """Sélectionne un agent puis passe en vue graphe."""
        self.click_text("Agents")
        self.wait(0.3)
        self.click_text(agent_name)
        self.wait(0.3)
        self.click_text("Graphe")

    def expand_all(self):
        """Clique sur le bouton 'Tout déplier'."""
        self.click("button:has-text('Tout déplier')", wait_after=3)

    def get_console_logs(self):
        return self.eval("() => console.logs || []")
