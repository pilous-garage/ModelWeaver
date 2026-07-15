"""ReviewService — Orchestre la validation recursive des tâches.

Le ReviewService surveille les tâches marquées comme terminées et, 
si elles nécessitent une revue, crée une tâche de critique associée.
"""

import logging
from typing import Any, Dict, List, Optional
from sql.db import ModelWeaverDB

logger = logging.getLogger("modelweaver.review")


class ReviewService:
    """Service de validation qualitative des résultats d'agents."""

    def __init__(self, db: ModelWeaverDB, dispatcher: Any):
        self.db = db
        self.dispatcher = dispatcher

    def process_completed_tasks(self) -> int:
        """Analyse les tâches DONE et déclenche des revues si nécessaire.
        
        Retourne le nombre de revues créées.
        """
        # On cherche les tâches DONE qui n'ont pas encore été revues
        # (on considère qu'une tâche est revue si elle a un enfant 'critique')
        completed_tasks = self.db.shared_tasks.list_done_without_review()
        if not completed_tasks:
            return 0

        review_count = 0
        for task in completed_tasks:
            if self._should_be_reviewed(task):
                if self._create_review_task(task):
                    review_count += 1
        
        self.db.commit()
        return review_count

    def _should_be_reviewed(self, task: Dict[str, Any]) -> bool:
        """Détermine si une tâche nécessite une validation (basé sur le rôle ou la priorité)."""
        role = task.get("required_role")
        # On demande systématiquement une revue pour les rôles critiques (codeur, architecte)
        critical_roles = ["codeur", "architecte", "planificateur"]
        if role in critical_roles:
            return True
        
        # Ou si la priorité est élevée
        if task.get("priority", 0) > 5:
            return True
            
        return False

    def _create_review_task(self, parent_task: Dict[str, Any]) -> bool:
        """Crée une tâche de critique liée à la tâche originale."""
        task_id = parent_task["task_id"]
        title = f"Review: {parent_task['title']}"
        description = (
            f"Vérifier la qualité du résultat de la tâche #{task_id}.\n"
            f"Critères: Exactitude, conformité aux specs, absence de bugs.\n"
            f"Si OK -> Marquer DONE.\n"
            f"Si KO -> Marquer FAILED avec feedback détaillé."
        )
        
        # On crée la tâche de revue
        review_id = self.db.shared_tasks.create(
            title=title,
            description=description,
            required_role="critique",
            context=parent_task["context"],
            parent_task_id=task_id,
            priority=parent_task.get("priority", 0) + 1
        )
        
        logger.info("ReviewService: Tâche de revue #%d créée pour la tâche #%d", review_id, task_id)
        return True

    def handle_review_result(self, review_task_id: int, status: str, feedback: str):
        """Traite le résultat d'une revue et met à jour la tâche parente."""
        review_task = self.db.shared_tasks.get(review_task_id)
        parent_id = review_task["parent_task_id"]
        if not parent_id:
            return

        if status == "DONE":
            # La revue est positive -> on valide définitivement la tâche parente
            # (elle est déjà DONE, on pourrait ajouter un flag 'validated')
            logger.info("ReviewService: Tâche #%d validée par la revue #%d", parent_id, review_task_id)
        elif status == "FAILED":
            # La revue est négative -> on remet la tâche parente en TODO avec le feedback
            logger.warning("ReviewService: Tâche #%d rejetée. Retour en TODO. Feedback: %s", parent_id, feedback)
            self.db.conn.execute(
                "UPDATE shared_tasks SET status='TODO', description=description || '\\n\\n[FEEDBACK REVUE]: ' || ? WHERE task_id=?",
                (feedback, parent_id)
            )
        
        self.db.commit()
