"""
Module Dependency Resolution System

Resolves module dependencies from metadata, calculates deployment order,
detects circular dependencies, and generates dependency graphs.
"""

from dataclasses import dataclass


class DependencyResolutionError(Exception):
    """Base exception for dependency resolution errors"""
    pass


class CircularDependencyError(DependencyResolutionError):
    """Raised when circular dependency is detected"""
    pass


class MissingDependencyError(DependencyResolutionError):
    """Raised when required dependency doesn't exist"""
    pass


@dataclass
class DependencyNode:
    """Represents a module node in dependency graph"""
    id: str
    name: str
    layer: str
    deployment_order: int


@dataclass
class DependencyEdge:
    """Represents a dependency relationship"""
    from_module: str
    to_module: str
    edge_type: str  # "required" or "optional"
    reason: str


class ModuleDependencyResolver:
    """
    Resolves module dependencies from metadata

    Usage:
        metadata = load_all_module_metadata("/path/to/modules")
        resolver = ModuleDependencyResolver(metadata)
        result = resolver.resolve(["bnk/gateway"])
        # result = {
        #     "required": ["bnk/far-setup", "bnk/flo", "bnk/bnk-gatewayclass", "bnk/gateway"],
        #     "optional": [],
        #     "deployment_order": ["bnk/far-setup", "bnk/flo", "bnk/bnk-gatewayclass", "bnk/gateway"]
        # }
    """

    def __init__(self, modules_metadata: dict[str, dict]):
        """
        Initialize resolver with module metadata

        Args:
            modules_metadata: Dict mapping module paths to their metadata
        """
        self.metadata = modules_metadata

    def resolve(
        self,
        selected_modules: list[str],
        include_optional: bool = False
    ) -> dict[str, list[str]]:
        """
        Resolve all dependencies for selected modules

        Args:
            selected_modules: List of module paths to resolve
            include_optional: Whether to include optional dependencies

        Returns:
            Dictionary with:
                - "required": List of required module paths (including transitive)
                - "optional": List of optional module paths
                - "deployment_order": Modules in deployment order

        Raises:
            DependencyResolutionError: If module not found
            CircularDependencyError: If circular dependency detected
            MissingDependencyError: If required dependency missing
        """
        # Validate selected modules exist
        for module_path in selected_modules:
            if module_path not in self.metadata:
                raise DependencyResolutionError(
                    f"Module '{module_path}' not found in metadata"
                )

        required_modules: set[str] = set()
        optional_modules: set[str] = set()
        visited: set[str] = set()
        path_stack: list[str] = []

        # Resolve dependencies for each selected module
        for module_path in selected_modules:
            self._resolve_recursive(
                module_path,
                required_modules,
                optional_modules,
                visited,
                path_stack,
                include_optional,
                is_optional=False
            )

        # Convert to sorted lists
        required_list = sorted(required_modules, key=lambda m: self._get_deployment_order(m))
        optional_list = sorted(optional_modules, key=lambda m: self._get_deployment_order(m))

        return {
            "required": required_list,
            "optional": optional_list,
            "deployment_order": required_list
        }

    def _resolve_recursive(
        self,
        module_path: str,
        required_modules: set[str],
        optional_modules: set[str],
        visited: set[str],
        path_stack: list[str],
        include_optional: bool,
        is_optional: bool
    ):
        """
        Recursively resolve dependencies

        Args:
            module_path: Current module to resolve
            required_modules: Set to collect required modules
            optional_modules: Set to collect optional modules
            visited: Set of already visited modules (for cycle detection)
            path_stack: Current dependency path (for cycle detection)
            include_optional: Whether to include optional dependencies
            is_optional: Whether current module is optional dependency
        """
        # Check for circular dependency
        if module_path in path_stack:
            cycle_path = " -> ".join(path_stack + [module_path])
            raise CircularDependencyError(
                f"Circular dependency detected: {cycle_path}"
            )

        # Add to appropriate set
        if is_optional:
            optional_modules.add(module_path)
        else:
            required_modules.add(module_path)

        # If already visited, no need to traverse again
        if module_path in visited:
            return

        visited.add(module_path)
        path_stack.append(module_path)

        # Get module metadata
        metadata = self.metadata.get(module_path)
        if not metadata:
            raise MissingDependencyError(
                f"Metadata for module '{module_path}' not found"
            )

        dependencies = metadata.get("dependencies", {})

        # Process required dependencies
        required_deps = dependencies.get("required", [])
        for dep in required_deps:
            dep_module = dep.get("module")
            if not dep_module:
                continue

            if dep_module not in self.metadata:
                raise MissingDependencyError(
                    f"Required dependency '{dep_module}' (required by '{module_path}') not found"
                )

            # Required dependencies are always required, even if parent is optional
            self._resolve_recursive(
                dep_module,
                required_modules,
                optional_modules,
                visited,
                path_stack,
                include_optional,
                is_optional=False
            )

        # Process optional dependencies
        if include_optional:
            optional_deps = dependencies.get("optional", [])
            for dep in optional_deps:
                dep_module = dep.get("module")
                if not dep_module:
                    continue

                if dep_module not in self.metadata:
                    # Optional dependency missing is OK, just skip
                    continue

                # Optional dependency stays optional
                self._resolve_recursive(
                    dep_module,
                    required_modules,
                    optional_modules,
                    visited,
                    path_stack,
                    include_optional,
                    is_optional=True
                )

        path_stack.pop()

    def get_dependency_graph(
        self,
        selected_modules: list[str],
        include_optional: bool = False
    ) -> dict:
        """
        Generate dependency graph for visualization

        Args:
            selected_modules: List of module paths
            include_optional: Whether to include optional dependencies

        Returns:
            Dictionary with:
                - "nodes": List of node objects (id, name, layer)
                - "edges": List of edge objects (from, to, type, reason)
        """
        # First resolve to get all modules
        resolution = self.resolve(selected_modules, include_optional)
        all_modules = set(resolution["required"] + resolution["optional"])

        # Build nodes
        nodes = []
        for module_path in all_modules:
            metadata = self.metadata.get(module_path, {})
            module_info = metadata.get("module", {})

            nodes.append({
                "id": module_path,
                "name": module_info.get("name", module_path),
                "layer": module_info.get("layer", "unknown"),
                "is_selected": module_path in selected_modules,
                "is_optional": module_path in resolution["optional"]
            })

        # Build edges
        edges = []
        for module_path in all_modules:
            metadata = self.metadata.get(module_path, {})
            dependencies = metadata.get("dependencies", {})

            # Required dependencies
            for dep in dependencies.get("required", []):
                dep_module = dep.get("module")
                if dep_module in all_modules:
                    edges.append({
                        "from": module_path,
                        "to": dep_module,
                        "type": "required",
                        "reason": dep.get("reason", "")
                    })

            # Optional dependencies
            if include_optional:
                for dep in dependencies.get("optional", []):
                    dep_module = dep.get("module")
                    if dep_module in all_modules:
                        edges.append({
                            "from": module_path,
                            "to": dep_module,
                            "type": "optional",
                            "reason": dep.get("reason", "")
                        })

        return {
            "nodes": nodes,
            "edges": edges
        }

    def get_missing_prerequisites(
        self,
        selected: list[str],
        already_deployed: list[str]
    ) -> list[str]:
        """
        Get list of modules that need to be deployed before selected modules

        Args:
            selected: Modules user wants to deploy
            already_deployed: Modules already deployed

        Returns:
            List of module paths that are required but not yet deployed
        """
        # Resolve all dependencies
        resolution = self.resolve(selected, include_optional=False)
        required = set(resolution["required"])

        # Remove already deployed
        deployed_set = set(already_deployed)
        missing = required - deployed_set

        # Sort by deployment order
        missing_list = sorted(missing, key=lambda m: self._get_deployment_order(m))

        return missing_list

    def _get_deployment_order(self, module_path: str) -> int:
        """Get deployment order for a module (for sorting)"""
        metadata = self.metadata.get(module_path, {})
        deployment = metadata.get("deployment", {})
        return deployment.get("order", 999)

    def validate_dependencies(self) -> list[str]:
        """
        Validate all dependencies in metadata

        Returns:
            List of validation errors (empty if all valid)
        """
        errors = []

        for module_path, metadata in self.metadata.items():
            dependencies = metadata.get("dependencies", {})

            # Check required dependencies exist
            for dep in dependencies.get("required", []):
                dep_module = dep.get("module")
                if dep_module and dep_module not in self.metadata:
                    errors.append(
                        f"Module '{module_path}' has missing required dependency: '{dep_module}'"
                    )

            # Check for self-dependency
            for dep in dependencies.get("required", []) + dependencies.get("optional", []):
                dep_module = dep.get("module")
                if dep_module == module_path:
                    errors.append(
                        f"Module '{module_path}' has invalid self-dependency"
                    )

        # Check for circular dependencies
        for module_path in self.metadata.keys():
            try:
                self.resolve([module_path])
            except CircularDependencyError as e:
                errors.append(str(e))

        return errors

    def get_deployment_layers(
        self,
        selected_modules: list[str],
        include_optional: bool = False
    ) -> dict[str, list[str]]:
        """
        Group modules by layer for staged deployment

        Args:
            selected_modules: List of module paths
            include_optional: Whether to include optional dependencies

        Returns:
            Dictionary mapping layer names to lists of module paths
        """
        resolution = self.resolve(selected_modules, include_optional)
        all_modules = resolution["required"]

        layers = {}
        for module_path in all_modules:
            metadata = self.metadata.get(module_path, {})
            module_info = metadata.get("module", {})
            layer = module_info.get("layer", "unknown")

            if layer not in layers:
                layers[layer] = []

            layers[layer].append(module_path)

        # Sort modules within each layer by deployment order
        for layer in layers:
            layers[layer] = sorted(
                layers[layer],
                key=lambda m: self._get_deployment_order(m)
            )

        return layers

    def get_dependency_summary(
        self,
        selected_modules: list[str],
        include_optional: bool = False
    ) -> dict:
        """
        Get comprehensive dependency information

        Args:
            selected_modules: List of module paths
            include_optional: Whether to include optional dependencies

        Returns:
            Dictionary with full dependency information
        """
        resolution = self.resolve(selected_modules, include_optional)
        graph = self.get_dependency_graph(selected_modules, include_optional)
        layers = self.get_deployment_layers(selected_modules, include_optional)

        return {
            "selected_modules": selected_modules,
            "required_dependencies": resolution["required"],
            "optional_dependencies": resolution["optional"],
            "deployment_order": resolution["deployment_order"],
            "deployment_layers": layers,
            "graph": graph,
            "total_modules": len(resolution["required"]) + len(resolution["optional"])
        }
