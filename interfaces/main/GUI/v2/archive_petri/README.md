# Archive — task_flow (réseau de Pétri + folding)

Code archivé lors de la reprise « depuis le début » (2026-08-10). À réincorporer
dans le frontend **au fur et à mesure**, étape par étape, en validant à chaque fois.

## Contenu

- `petriFold.ts` — moteur de réseau de Pétri :
  - `fsmToPetri` (nœuds → places, arêtes → transitions)
  - `flattenFsm` (unfold ALL, `vars.box` = appartenance)
  - `absorbConditionOuts` (branches de switch → arêtes)
  - folding interactif : `findFolds`/`findSeqFolds`/`findParFolds` (règle xAy→z, séq + par),
    `applyFolds`/`applyFoldOnce` (rebranche les inner des petri-box), `autoFold`
  - `petriToGraph` (conversion GraphDoc)
- `agent_petri.ts` — fonctions du panneau (`agentYamlToPetri`, `addPetriTokens`,
  `agentTokenSignature`, `agentsPetriGraph`). Imports manquants (extrait du panel) :
  `agentYamlToGraph`, `prefixGraphIds` (du panel) et `fsmToPetri`/`flattenFsm`/
  `absorbConditionOuts`/`petriToGraph`/`PetriNet` (de `petriFold.ts`).

## État au moment de l'archivage

- Construire le FSM → tout déplier (unfold all) → transformer (arêtes → transitions) →
  consommations/productions → **0 fold** → tables de fold initialisées.
- Les boxes (petri-box) sont toujours **dépliées** (dérivées synchrone) et non repliables.
- Les skills dépliés montrent leurs inputs/outputs (contrats).
- Bug restant signalé : certains skills encore repliés au rendu (voir séquence de logs).
