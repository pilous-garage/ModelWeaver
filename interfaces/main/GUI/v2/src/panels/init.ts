// Enregistrement de tous les panels au démarrage.
import { registerPanel, loadAllPanelLangs } from './registry.ts';
import { Panel as Ressources } from './ressources.panel.tsx';
import { Panel as EtatSysteme } from './etat-systeme.panel.tsx';
import { RessourcesVariantPanel, EtatSimplePanel } from './test-panels.panel.tsx';
import { Panel as MonitoringProcessus } from './monitoring-processus.panel.tsx';
import { Panel as MonitoringProjets } from './monitoring-projets.panel.tsx';
import { Panel as ProjetWorkspace } from './projet-workspace.panel.tsx';
import { Panel as GestionOutils } from './gestion-outils.panel.tsx';
import { Panel as GestionBundles } from './gestion-bundles.panel.tsx';
import { Panel as AgentsMonitoring } from './agents-monitoring.panel.tsx';
import { Panel as MonitoringLlmDistant } from './monitoring-llm-distant.panel.tsx';
import { Panel as SystemeRessources } from './systeme-ressources.panel.tsx';
import { Panel as SystemeEtat } from './systeme-etat.panel.tsx';
import { Panel as InstallatorFileQueue } from './installator-file-queue.panel.tsx';
import { Panel as InstallatorOutilsInstalles } from './installator-outils-installes.panel.tsx';
import { Panel as InstallatorDashboard } from './installator-dashboard.panel.tsx';
import { Panel as DebugServices } from './debug-services.panel.tsx';
import { Panel as GestionClesApi } from './gestion-cles-api.panel.tsx';
import { Panel as SystemeLlmLocaux } from './systeme-llm-locaux.panel.tsx';
import { Panel as DockerRessources } from './docker-ressources.panel.tsx';
import { Panel as AgentsListe } from './agents-liste.panel.tsx';
import { Panel as AgentsLanceur } from './agents-lanceur.panel.tsx';
import { Panel as AgentsEquipe } from './agents-equipe.panel.tsx';
import { Panel as AgentsTopologie } from './agents-topologie.panel.tsx';
import { Panel as GestionPanneaux } from './gestion-panneaux.panel.tsx';
import { Panel as DebugLogs } from './debug-logs.panel.tsx';
import { Panel as InstallatorDeps } from './installator-deps.panel.tsx';
import { Panel as GestionCatalogueModeles } from './gestion-catalogue-modeles.panel.tsx';
import { Panel as CommunicationChat } from './communication-chat.panel.tsx';
import { Panel as SystemeDashboard } from './systeme-dashboard.panel.tsx';
import { Panel as ProjetEquipes } from './projet-equipes.panel.tsx';
import { Panel as AgentsCompositionEquipe } from './agents-composition-equipe.panel.tsx';
import { Panel as MonitoringWorkspace } from './monitoring-workspace.panel.tsx';
import { Panel as AgentsTeamMembers } from './agents-team-members.panel.tsx';
import { Panel as AgentsActivity } from './agents-activity.panel.tsx';
import { Panel as CommunicationDevChat } from './communication-dev-chat.panel.tsx';
import { Panel as AgentsEquipeLeger } from './agents-equipe-leger.panel.tsx';
import { Panel as MonitoringLlmAvance } from './monitoring-llm-avance.panel.tsx';
import { Panel as Autorisations } from './autorisations.panel.tsx';
import { Panel as ViewAgent } from './view-agent.panel.tsx';
import { Panel as AgentTaskflow } from './agent-taskflow.panel.tsx';
import { Panel as SwarmTaskflow } from './swarm-taskflow.panel.tsx';
import { Panel as GrapheAgent } from './graphe-agent.panel.tsx';

/** Enregistre tous les panels + charge leurs lang. */
export async function initPanels(): Promise<void> {
  registerPanel(Ressources);
  registerPanel(EtatSysteme);
  registerPanel(RessourcesVariantPanel);
  registerPanel(EtatSimplePanel);
  // Panels migrés de V1
  registerPanel(MonitoringProcessus);
  registerPanel(MonitoringProjets);
  registerPanel(ProjetWorkspace);
  registerPanel(GestionOutils);
  registerPanel(GestionBundles);
  registerPanel(AgentsMonitoring);
  registerPanel(MonitoringLlmDistant);
  registerPanel(SystemeRessources);
  registerPanel(SystemeEtat);
  registerPanel(InstallatorFileQueue);
  registerPanel(InstallatorOutilsInstalles);
  registerPanel(InstallatorDashboard);
  // Panels migrés de V1 (2e lot : découplés de useApp)
  registerPanel(DebugServices);
  registerPanel(GestionClesApi);
  registerPanel(SystemeLlmLocaux);
  // Panels migrés de V1 (3e lot : HTTP pur)
  registerPanel(DockerRessources);
  registerPanel(AgentsListe);
  registerPanel(AgentsLanceur);
  registerPanel(AgentsEquipe);
  registerPanel(AgentsTopologie);
  registerPanel(GestionPanneaux);
  registerPanel(DebugLogs);
  registerPanel(InstallatorDeps);
  // Panels migrés de V1 (4e lot : HTTP pur)
  registerPanel(GestionCatalogueModeles);
  registerPanel(CommunicationChat);
  registerPanel(SystemeDashboard);
  // Alias V1 (réutilisent agents-equipe)
  registerPanel(ProjetEquipes);
  registerPanel(AgentsCompositionEquipe);
  // Moniteur workspace (issues + tâches greedy)
  registerPanel(MonitoringWorkspace);
  // Moniteur team + contrôle membres à chaud
  registerPanel(AgentsTeamMembers);
  // Moniteur avancé agents (FSM step + conversation)
  registerPanel(AgentsActivity);
  // Chat de dev (agent pilote plan/build)
  registerPanel(CommunicationDevChat);
  // Monitoring léger de la team (3 colonnes de carrés)
  registerPanel(AgentsEquipeLeger);
  // Demandes d'autorisation (membres → leader → humain)
  registerPanel(Autorisations);
  registerPanel(ViewAgent);
  // Graphes : taskflow agent (dépliage) + swarmflow (toutes teams)
  registerPanel(AgentTaskflow);
  registerPanel(SwarmTaskflow);
  // Graphe Agent : catalogue + YAML/graphe (lecture seule)
  registerPanel(GrapheAgent);
  // Monitoring LLM avancé (graphes token/min, req/min par dim)
  registerPanel(MonitoringLlmAvance);
  await loadAllPanelLangs();
}
