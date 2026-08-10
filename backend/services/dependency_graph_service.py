"""
Dependency Graph Service for BNK-Forge v2

Analyzes module dependencies to enable parallel execution.
Creates execution layers where modules in the same layer can run in parallel.

B4: Uses Kahn's algorithm for O(N+E) topological sort with integrated cycle detection.
"""

import logging
from collections import deque

from sqlalchemy.orm import Session, joinedload

from models import ModuleLibrary, ProjectModule

logger = logging.getLogger(__name__)


def _kahn_layers(
    module_map: dict[int, ProjectModule],
    module_ids: set[int],
    *,
    filter_deps_to_set: bool = False,
) -> list[list[ProjectModule]]:
    """
    Kahn's algorithm: BFS-based topological sort that produces layers.

    Complexity: O(N + E) where N = number of modules, E = total dependency edges.

    Each layer contains modules whose in-degree (within the working set) has
    reached zero, meaning all their dependencies are satisfied by earlier layers.
    Modules within a single layer can be executed in parallel.

    Cycle detection is inherent: if any nodes remain unprocessed after the BFS
    exhausts the queue, those nodes are part of one or more cycles.

    Args:
        module_map: Mapping of module ID -> ProjectModule.
        module_ids: Set of module IDs to consider (the working set).
        filter_deps_to_set: When True, only dependencies that exist inside
            ``module_ids`` are considered (used by ``build_layers_for_modules``
            for subset operations).

    Returns:
        List of layers (each a list of ProjectModule objects).

    Raises:
        ValueError: If a cycle is detected among the modules.
    """
    if not module_ids:
        return []

    # ------------------------------------------------------------------
    # 1. Build adjacency list (dependents) and in-degree counts
    #    Edge direction: dependency -> dependent  (dep_id -> module_id)
    # ------------------------------------------------------------------
    in_degree: dict[int, int] = {mid: 0 for mid in module_ids}
    # dependents[dep_id] = list of module_ids that depend on dep_id
    dependents: dict[int, list[int]] = {mid: [] for mid in module_ids}

    for mid in module_ids:
        module = module_map[mid]
        deps = module.dependencies or []
        for dep_id in deps:
            if filter_deps_to_set and dep_id not in module_ids:
                continue  # skip deps outside the working set
            if dep_id in module_ids:
                in_degree[mid] += 1
                dependents[dep_id].append(mid)
            # deps referencing IDs outside module_ids (and filter not set)
            # are ignored — they are already deployed / external

    # ------------------------------------------------------------------
    # 2. Seed the BFS queue with zero-in-degree nodes (layer 0)
    # ------------------------------------------------------------------
    queue: deque[int] = deque()
    for mid in module_ids:
        if in_degree[mid] == 0:
            queue.append(mid)

    # ------------------------------------------------------------------
    # 3. Process layer by layer
    # ------------------------------------------------------------------
    layers: list[list[ProjectModule]] = []
    processed_count = 0

    while queue:
        # All nodes currently in the queue form the next layer
        layer_size = len(queue)
        current_layer: list[ProjectModule] = []

        for _ in range(layer_size):
            mid = queue.popleft()
            current_layer.append(module_map[mid])
            processed_count += 1

            # Decrease in-degree for each dependent
            for dependent_id in dependents[mid]:
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

        layers.append(current_layer)

    # ------------------------------------------------------------------
    # 4. Cycle detection: if not all nodes were processed, a cycle exists
    # ------------------------------------------------------------------
    if processed_count < len(module_ids):
        # Identify the nodes trapped in cycles for a useful error message
        cycle_ids = {mid for mid in module_ids if in_degree[mid] > 0}
        cycle_names = []
        for mid in cycle_ids:
            m = module_map[mid]
            if m.library_module:
                cycle_names.append(m.library_module.name)
            else:
                cycle_names.append(f"ID:{mid}")
        raise ValueError(
            f"Circular dependency detected among modules: {', '.join(sorted(cycle_names))}"
        )

    return layers


class DependencyGraphService:
    """
    Service for analyzing module dependency graphs.

    Provides:
    - Layer-based grouping for parallel execution
    - Cycle detection in dependency graphs
    - Execution order calculation

    Uses Kahn's algorithm (BFS topological sort) for O(N+E) performance.
    """

    def __init__(self, db: Session):
        self.db = db

    def build_layers(self, project_id: int) -> list[list[ProjectModule]]:
        """
        Build dependency layers for parallel execution.

        Modules are grouped into layers where:
        - Layer 0: Modules with no dependencies
        - Layer 1: Modules that depend only on Layer 0
        - Layer N: Modules that depend on Layer N-1 or earlier

        All modules within a layer can be executed in parallel.

        Uses Kahn's algorithm for O(N+E) topological sort with integrated
        cycle detection.

        Args:
            project_id: Project ID to analyze

        Returns:
            List of layers, each containing modules that can run in parallel

        Raises:
            ValueError: If circular dependencies are detected
        """
        # S14-005: Get only ENABLED modules for project with eager loading
        # Disabled modules should not be included in deployment layers
        modules = self.db.query(ProjectModule).options(
            joinedload(ProjectModule.library_module).load_only(
                ModuleLibrary.id, ModuleLibrary.name,
            ),
        ).filter(
            ProjectModule.project_id == project_id,
            ProjectModule.enabled
        ).all()

        if not modules:
            return []

        module_map = {m.id: m for m in modules}
        module_ids = set(module_map.keys())

        layers = _kahn_layers(module_map, module_ids)

        for layer_idx, layer in enumerate(layers):
            logger.debug(
                f"Layer {layer_idx}: {len(layer)} modules - "
                f"{[m.library_module.name if m.library_module else f'ID:{m.id}' for m in layer]}"
            )

        logger.info(f"Built {len(layers)} dependency layers for project {project_id}")
        return layers

    def get_execution_order(self, project_id: int) -> list[list[int]]:
        """
        Get module IDs grouped by execution layer.

        Returns list of layers, where each layer is a list of module IDs
        that can be executed in parallel.

        Args:
            project_id: Project ID

        Returns:
            List of layers containing module IDs: [[layer0_ids], [layer1_ids], ...]

        Example:
            [[1], [2, 3], [4]] means:
            - Layer 0: Module 1
            - Layer 1: Modules 2 and 3 (parallel)
            - Layer 2: Module 4
        """
        layers = self.build_layers(project_id)
        return [[m.id for m in layer] for layer in layers]

    def validate_no_cycles(self, project_id: int) -> tuple[bool, list[int] | None]:
        """
        Check for circular dependencies using Kahn's algorithm.

        Kahn's algorithm naturally detects cycles: if not all nodes are
        processed during the BFS, the remaining nodes are part of a cycle.

        Args:
            project_id: Project ID to check

        Returns:
            Tuple of (is_valid, cycle_path_if_found)
            - is_valid: True if no cycles, False if cycle detected
            - cycle_path: List of module IDs involved in cycles (if found), None otherwise
        """
        # Get all modules (not just enabled — cycle check is project-wide)
        modules = self.db.query(ProjectModule).filter(
            ProjectModule.project_id == project_id
        ).all()

        if not modules:
            return True, None

        module_map = {m.id: m for m in modules}
        module_ids = set(module_map.keys())

        try:
            _kahn_layers(module_map, module_ids)
            logger.debug(f"No circular dependencies found in project {project_id}")
            return True, None
        except ValueError:
            # Identify cycle members via in-degree analysis
            in_degree: dict[int, int] = {mid: 0 for mid in module_ids}
            for mid in module_ids:
                deps = module_map[mid].dependencies or []
                for dep_id in deps:
                    if dep_id in module_ids:
                        in_degree[mid] += 1

            # BFS to remove non-cycle nodes
            queue: deque[int] = deque(
                mid for mid in module_ids if in_degree[mid] == 0
            )
            while queue:
                mid = queue.popleft()
                for other_id in module_ids:
                    m = module_map[other_id]
                    if m.dependencies and mid in m.dependencies:
                        in_degree[other_id] -= 1
                        if in_degree[other_id] == 0:
                            queue.append(other_id)

            cycle_ids = [mid for mid in module_ids if in_degree[mid] > 0]
            logger.warning(
                f"Circular dependency detected in project {project_id}: {cycle_ids}"
            )
            return False, cycle_ids

    def get_dependency_graph_stats(self, project_id: int) -> dict:
        """
        Get statistics about the dependency graph.

        Args:
            project_id: Project ID

        Returns:
            Dict with statistics:
            {
                "total_modules": int,
                "total_layers": int,
                "max_parallel_modules": int,
                "avg_modules_per_layer": float,
                "modules_with_no_deps": int,
                "modules_with_deps": int
            }
        """
        try:
            layers = self.build_layers(project_id)

            if not layers:
                return {
                    "total_modules": 0,
                    "total_layers": 0,
                    "max_parallel_modules": 0,
                    "avg_modules_per_layer": 0,
                    "modules_with_no_deps": 0,
                    "modules_with_deps": 0
                }

            total_modules = sum(len(layer) for layer in layers)
            max_parallel = max(len(layer) for layer in layers)
            avg_per_layer = total_modules / len(layers)
            no_deps = len(layers[0])  # Layer 0 has no dependencies
            with_deps = total_modules - no_deps

            return {
                "total_modules": total_modules,
                "total_layers": len(layers),
                "max_parallel_modules": max_parallel,
                "avg_modules_per_layer": round(avg_per_layer, 2),
                "modules_with_no_deps": no_deps,
                "modules_with_deps": with_deps
            }

        except ValueError as e:
            # Circular dependency or other error
            logger.error(f"Error getting graph stats: {e}")
            return {
                "error": str(e),
                "total_modules": 0,
                "total_layers": 0
            }

    def get_module_dependencies_recursive(self, module_id: int) -> set[int]:
        """
        Get all dependencies of a module recursively (transitive closure).

        Args:
            module_id: Module ID

        Returns:
            Set of all module IDs that this module depends on (directly or indirectly)
        """
        module = self.db.query(ProjectModule).filter(
            ProjectModule.id == module_id
        ).first()

        if not module or not module.dependencies:
            return set()

        all_deps = set()
        to_visit = list(module.dependencies)

        while to_visit:
            dep_id = to_visit.pop()
            if dep_id in all_deps:
                continue  # Already visited

            all_deps.add(dep_id)

            # Get dependencies of this dependency
            dep_module = self.db.query(ProjectModule).filter(
                ProjectModule.id == dep_id
            ).first()

            if dep_module and dep_module.dependencies:
                to_visit.extend(dep_module.dependencies)

        return all_deps

    def get_reverse_dependencies(self, module_id: int, project_id: int) -> list[ProjectModule]:
        """
        Get all modules that depend on this module.

        Args:
            module_id: Module ID
            project_id: Project ID to search in

        Returns:
            List of modules that depend on the given module
        """
        modules = self.db.query(ProjectModule).options(
            joinedload(ProjectModule.library_module).load_only(
                ModuleLibrary.id, ModuleLibrary.name,
            ),
        ).filter(
            ProjectModule.project_id == project_id
        ).all()

        dependents = []
        for module in modules:
            if module.dependencies and module_id in module.dependencies:
                dependents.append(module)

        return dependents

    def build_layers_for_modules(
        self,
        modules: list[ProjectModule],
        reverse: bool = False
    ) -> list[list[ProjectModule]]:
        """
        Build dependency layers for a specific set of modules.

        Used for stack operations where we need layers for a subset of project modules.

        Uses Kahn's algorithm for O(N+E) topological sort. Only considers
        dependencies within the provided module set (external deps are ignored).

        Args:
            modules: List of ProjectModule objects to organize into layers
            reverse: If True, return layers in reverse order (for destroy operations)

        Returns:
            List of layers, where each layer contains modules that can be executed in parallel.
            For deploy: Layer 0 = no deps, Layer N = depends on earlier layers
            For destroy (reverse=True): Layer 0 = leaf nodes, Layer N = root nodes
        """
        if not modules:
            return []

        module_map = {m.id: m for m in modules}
        module_ids = set(module_map.keys())

        layers = _kahn_layers(module_map, module_ids, filter_deps_to_set=True)

        if reverse:
            layers = list(reversed(layers))
            logger.debug(f"Built {len(layers)} reverse dependency layers for {len(modules)} modules")
        else:
            logger.debug(f"Built {len(layers)} dependency layers for {len(modules)} modules")

        return layers
