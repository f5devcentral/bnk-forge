"""
Dependency Graph Utilities for Module Dependency Management

Provides functions for:
- Circular dependency detection
- Topological sorting for deployment order
- Dependency validation
- Graph structure generation for visualization
"""

from collections import defaultdict, deque


class CircularDependencyError(Exception):
    """Raised when a circular dependency is detected."""
    pass


class DependencyValidationError(Exception):
    """Raised when dependency validation fails."""
    pass


def detect_circular_dependencies(dependencies: dict[int, list[int]]) -> list[int] | None:
    """
    Detect circular dependencies in a dependency graph using DFS.

    Args:
        dependencies: Dict mapping module_id -> list of dependency module_ids

    Returns:
        List of module IDs forming the cycle if found, None otherwise

    Example:
        dependencies = {1: [2], 2: [3], 3: [1]}  # Circular: 1->2->3->1
        result = detect_circular_dependencies(dependencies)
        # Returns: [1, 2, 3, 1]
    """
    # Track visited nodes and recursion stack
    WHITE, GRAY, BLACK = 0, 1, 2
    color = defaultdict(lambda: WHITE)

    def dfs(node: int, path: list[int]) -> list[int] | None:
        """DFS to detect cycles."""
        if color[node] == GRAY:
            # Found a cycle - return the cycle path
            cycle_start = path.index(node)
            return path[cycle_start:] + [node]

        if color[node] == BLACK:
            return None

        color[node] = GRAY
        path.append(node)

        for neighbor in dependencies.get(node, []):
            cycle = dfs(neighbor, path[:])
            if cycle:
                return cycle

        color[node] = BLACK
        return None

    # Check all nodes (for disconnected components)
    for node in dependencies:
        if color[node] == WHITE:
            cycle = dfs(node, [])
            if cycle:
                return cycle

    return None


def topological_sort(modules: list[dict], dependencies: dict[int, list[int]]) -> list[int]:
    """
    Perform topological sort on modules based on dependencies using Kahn's algorithm.

    Args:
        modules: List of module dictionaries with 'id' field
        dependencies: Dict mapping module_id -> list of dependency module_ids

    Returns:
        List of module IDs in topological order (dependencies first)

    Raises:
        CircularDependencyError: If circular dependencies are detected

    Example:
        modules = [{'id': 1}, {'id': 2}, {'id': 3}]
        dependencies = {2: [1], 3: [1, 2]}  # 3 depends on 1,2; 2 depends on 1
        result = topological_sort(modules, dependencies)
        # Returns: [1, 2, 3]
    """
    # First check for circular dependencies
    cycle = detect_circular_dependencies(dependencies)
    if cycle:
        raise CircularDependencyError(
            f"Circular dependency detected: {' -> '.join(map(str, cycle))}"
        )

    # Build graph and compute in-degrees
    module_ids = {m['id'] for m in modules}
    in_degree = {mid: 0 for mid in module_ids}
    graph = defaultdict(list)

    # Build adjacency list and calculate in-degrees
    for module_id, deps in dependencies.items():
        if module_id not in module_ids:
            continue
        for dep_id in deps:
            if dep_id in module_ids:
                graph[dep_id].append(module_id)
                in_degree[module_id] += 1

    # Initialize queue with nodes that have no dependencies
    queue = deque([mid for mid in module_ids if in_degree[mid] == 0])
    result = []

    while queue:
        # Remove a node with no incoming edges
        current = queue.popleft()
        result.append(current)

        # Reduce in-degree for neighbors
        for neighbor in graph[current]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # If result doesn't contain all nodes, there was a cycle
    # (this shouldn't happen as we check earlier, but safety check)
    if len(result) != len(module_ids):
        raise CircularDependencyError("Circular dependency detected during sort")

    return result


def validate_dependencies(
    module_id: int,
    dependency_ids: list[int],
    project_id: int,
    all_modules: list[dict]
) -> tuple[bool, str | None]:
    """
    Validate that dependencies are valid for a module.

    Checks:
    1. All dependency modules exist
    2. All dependency modules are in the same project
    3. Module doesn't depend on itself
    4. Adding these dependencies won't create a circular dependency

    Args:
        module_id: ID of the module being updated
        dependency_ids: List of dependency module IDs to validate
        project_id: Project ID the module belongs to
        all_modules: List of all modules in the project

    Returns:
        Tuple of (is_valid, error_message)

    Example:
        is_valid, error = validate_dependencies(3, [1, 2], 1, modules)
    """
    # Get all module IDs in project
    project_module_ids = {m['id'] for m in all_modules if m.get('project_id') == project_id}

    # Check 1: Self-dependency
    if module_id in dependency_ids:
        return False, "Module cannot depend on itself"

    # Check 2: All dependencies exist in same project
    for dep_id in dependency_ids:
        if dep_id not in project_module_ids:
            return False, f"Dependency module {dep_id} not found in project"

    # Check 3: Build dependency graph and check for cycles
    # Create a hypothetical dependency graph with the new dependencies
    dependencies = {}
    for module in all_modules:
        if module.get('project_id') != project_id:
            continue

        mod_id = module['id']
        if mod_id == module_id:
            # Use the new dependencies for this module
            dependencies[mod_id] = dependency_ids
        else:
            # Use existing dependencies
            dependencies[mod_id] = module.get('dependencies', [])

    # Check for circular dependencies
    cycle = detect_circular_dependencies(dependencies)
    if cycle:
        return False, f"Would create circular dependency: {' -> '.join(map(str, cycle))}"

    return True, None


def calculate_deployment_order(modules: list[dict]) -> dict[int, int]:
    """
    Calculate deployment order for all modules based on dependencies.

    Args:
        modules: List of module dictionaries with 'id' and 'dependencies' fields

    Returns:
        Dict mapping module_id -> deployment_order (0-based)

    Raises:
        CircularDependencyError: If circular dependencies are detected

    Example:
        modules = [
            {'id': 1, 'dependencies': []},
            {'id': 2, 'dependencies': [1]},
            {'id': 3, 'dependencies': [1, 2]}
        ]
        result = calculate_deployment_order(modules)
        # Returns: {1: 0, 2: 1, 3: 2}
    """
    # Build dependency graph
    dependencies = {}
    for module in modules:
        dependencies[module['id']] = module.get('dependencies', [])

    # Perform topological sort
    sorted_ids = topological_sort(modules, dependencies)

    # Assign deployment order
    return {module_id: order for order, module_id in enumerate(sorted_ids)}


def get_dependency_graph(modules: list[dict]) -> dict:
    """
    Generate dependency graph structure for visualization.

    Args:
        modules: List of module dictionaries with 'id', 'name', 'dependencies' fields

    Returns:
        Dict with 'nodes' and 'edges' for graph visualization

    Example:
        modules = [
            {'id': 1, 'name': 'VPC', 'dependencies': []},
            {'id': 2, 'name': 'Subnet', 'dependencies': [1]},
            {'id': 3, 'name': 'EKS', 'dependencies': [1, 2]}
        ]
        graph = get_dependency_graph(modules)
        # Returns: {
        #     'nodes': [
        #         {'id': 1, 'name': 'VPC'},
        #         {'id': 2, 'name': 'Subnet'},
        #         {'id': 3, 'name': 'EKS'}
        #     ],
        #     'edges': [
        #         {'from': 1, 'to': 2},
        #         {'from': 1, 'to': 3},
        #         {'from': 2, 'to': 3}
        #     ]
        # }
    """
    module_ids = {module['id'] for module in modules}
    dependencies = {
        module['id']: [d for d in module.get('dependencies', []) if d in module_ids]
        for module in modules
    }

    # Detect cycles up front. A cyclic graph cannot be layered, so we surface an
    # error on the payload (FEAT-0139 / ERR-0022) and leave layers unassigned.
    cycle = detect_circular_dependencies(dependencies)
    has_cycle = bool(cycle)
    layers = {} if has_cycle else _compute_layers(module_ids, dependencies)

    nodes = []
    edges = []

    for module in modules:
        # Add node
        nodes.append({
            'id': module['id'],
            'name': module.get('name', f"Module {module['id']}"),
            'status': module.get('status', 'unknown'),
            'path': module.get('path_in_project', ''),
            'deployment_order': module.get('deployment_order', 0),
            # Layer index for visualization (None when the graph is cyclic).
            'layer': layers.get(module['id']),
        })

        # Add edges for dependencies
        for dep_id in module.get('dependencies', []):
            edges.append({
                'from': dep_id,  # Dependency
                'to': module['id']  # Dependent module
            })

    result = {
        'nodes': nodes,
        'edges': edges,
        'layers': layers,
        'has_cycle': has_cycle,
    }
    if has_cycle:
        result['cycle'] = cycle
        result['error'] = (
            f"Circular dependency detected: {' -> '.join(map(str, cycle))}"
        )
    return result


def _compute_layers(module_ids: set[int], dependencies: dict[int, list[int]]) -> dict[int, int]:
    """Assign each module a layer index = longest dependency path to a root.

    Modules with no (in-graph) dependencies are layer 0; every other module sits
    one layer below its deepest dependency. Assumes an acyclic graph (callers
    detect cycles first).
    """
    layers: dict[int, int] = {}

    def layer_of(mid: int) -> int:
        if mid in layers:
            return layers[mid]
        deps = dependencies.get(mid, [])
        layer = 0 if not deps else max(layer_of(dep) for dep in deps) + 1
        layers[mid] = layer
        return layer

    for mid in module_ids:
        layer_of(mid)
    return layers


def get_dependent_modules(module_id: int, all_modules: list[dict]) -> list[int]:
    """
    Get all modules that depend on the given module.

    Args:
        module_id: ID of the module to check
        all_modules: List of all modules

    Returns:
        List of module IDs that depend on this module

    Example:
        modules = [
            {'id': 1, 'dependencies': []},
            {'id': 2, 'dependencies': [1]},
            {'id': 3, 'dependencies': [1]}
        ]
        dependents = get_dependent_modules(1, modules)
        # Returns: [2, 3]
    """
    dependents = []
    for module in all_modules:
        if module_id in module.get('dependencies', []):
            dependents.append(module['id'])
    return dependents


def check_dependencies_met(
    module: dict,
    all_modules: list[dict],
    required_statuses: list[str] = None
) -> tuple[bool, list[dict]]:
    """
    Check if all dependencies for a module are met (deployed).

    Args:
        module: Module dictionary to check
        all_modules: List of all modules
        required_statuses: List of acceptable status values (default: ['applied'])

    Returns:
        Tuple of (all_met, unmet_dependencies)
        where unmet_dependencies is a list of module dicts that aren't deployed

    Example:
        module = {'id': 3, 'dependencies': [1, 2]}
        is_met, unmet = check_dependencies_met(module, all_modules)
    """
    if required_statuses is None:
        required_statuses = ['applied']

    dependency_ids = module.get('dependencies', [])
    if not dependency_ids:
        return True, []

    # Create module lookup
    module_map = {m['id']: m for m in all_modules}

    unmet = []
    for dep_id in dependency_ids:
        dep_module = module_map.get(dep_id)
        if not dep_module:
            # Dependency doesn't exist (should have been caught by validation)
            unmet.append({'id': dep_id, 'name': 'Unknown', 'status': 'not_found'})
        elif dep_module.get('status') not in required_statuses:
            unmet.append(dep_module)

    return len(unmet) == 0, unmet
