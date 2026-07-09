"""AutoDebugService — Orchestrateur de la boucle Codeur -> TestRunner -> Debugger.

Ce service automatise le cycle de correction récursive :
1. Un Codeur termine une tâche.
2. Le TestRunner valide le code.
3. En cas d'échec, le Debugger analyse l'erreur.
4. Le Codeur reçoit le plan de correction et recommence.
"""

import logging
from typing import Any, Dict, List, Optional
from sql.db import ModelWeaverDB

logger = logging.getLogger("modelweaver.autodebug")


class AutoDebugService:
    """Gère la boucle récursive d'auto-correction du code."""

    def __init__(self, db: ModelWeaverDB, dispatcher: Any):
        self.db = db
        self.dispatcher = dispatcher

    def tick(self) -> int:
        """Vérifie l'état des tâches et fait avancer la boucle de debug.
        
        Retourne le nombre de nouvelles tâches créées.
        """
        # 1. Chercher des tâches de CODEUR terminées -> Lancer TEST_RUNNER
        count = self._handle_code_to_test()
        
        # 2. Chercher des tâches de TEST_RUNNER échouées -> Lancer DEBUGGER
        count += self._handle_test_to_debug()
        
        # 3. Chercher des tâches de DEBUGGER terminées -> Lancer CODEUR (correction)
        count += self._handle_debug_to_code()
        
        self.db.commit()
        return count

    def _handle_code_to_test(self) -> int:
        """Tâches CODEUR DONE -> Tâches TEST_RUNNER."""
        # On cherche les tâches DONE dont le rôle était 'codeur' et qui n'ont pas d'enfant 'test_runner'
        cur = self.db.conn.execute("""
            SELECT st.* FROM shared_tasks st
            WHERE st.status = 'DONE' AND st.required_role = 'codeur'
              AND NOT EXISTS (
                SELECT 1 FROM shared_tasks child 
                WHERE child.parent_task_id = st.task_id AND child.required_role = 'test_runner'
              )
        """)
        tasks = [dict(r) for r in cur.fetchall()]
        
        created = 0
        for task in tasks:
            title = f"Test: {task['title']}"
            desc = f"Valider le code produit pour la tâche #{task['task_id']}. \nSortie attendue: STATUS: PASSED ou STATUS: FAILED."
            self.db.shared_tasks.create(
                title=title,
                description=desc,
                required_role="test_runner",
                context=task["context"],
                parent_task_id=task["task_id"]
            )
            created += 1
            logger.info("AutoDebug: Lancement du TestRunner pour la tâche #%d", task["task_id"])
        
        return created

    def _handle_test_to_debug(self) -> int:
        """Tâches TEST_RUNNER FAILED -> Tâches DEBUGGER."""
        cur = self.db.conn.execute("""
            SELECT st.* FROM shared_tasks st
            WHERE st.status = 'FAILED' AND st.required_role = 'test_runner'
              AND NOT EXISTS (
                SELECT 1 FROM shared_tasks child 
                WHERE child.parent_task_id = st.task_id AND child.required_role = 'debugger'
              )
        """)
        tasks = [dict(r) for r in cur.fetchall()]
        
        created = 0
        for task in tasks:
            title = f"Debug: {task['title']}"
            desc = f"Analyser l'échec du test pour la tâche #{task['task_id']}. \nFournir un plan de correction précis pour le codeur."
            self.db.shared_tasks.create(
                title=title,
                description=desc,
                required_role="debugger",
                context=task["context"],
                parent_task_id=task["task_id"]
            )
            created += 1
            logger.info("AutoDebug: Lancement du Debugger suite à l'échec du test #%d", task["task_id"])
            
        return created

    def _handle_debug_to_code(self) -> int:
        """Tâches DEBUGGER DONE -> Tâches CODEUR (Correction)."""
        cur = self.db.conn.execute("""
            SELECT st.* FROM shared_tasks st
            WHERE st.status = 'DONE' AND st.required_role = 'debugger'
              AND NOT EXISTS (
                SELECT 1 FROM shared_tasks child 
                WHERE child.parent_task_id = st.task_id AND child.required_role = 'codeur'
              )
        """)
        tasks = [dict(r) for r in cur.fetchall()]
        
        created = 0
        for task in tasks:
            # On remonte à la tâche originale (grand-parent) pour garder le fil
            parent_id = task["parent_task_id"]
            parent_task = self.db.shared_tasks.get(parent_id)
            
            title = f"Fix: {parent_task['title'] if parent_task else task['title']}"
            desc = f"Appliquer la correction proposée par le debugger (Tâche #{task['task_id']})."
            
            self.db.shared_tasks.create(
                title=title,
                description=desc,
                required_role="codeur",
                context=task["context"],
                parent_task_id=task["task_id"]
            )
            created += 1
            logger.info("AutoDebug: Nouvelle tentative de correction lancée pour la tâche #%d", parent_id if parent_id else task["task_id"])
            
        return created
