"""
Input Wiring Service
Manages the wiring of module outputs to dependent module inputs.
"""
import logging

from sqlalchemy.orm import Session

from models import ModuleLibrary, ProjectModule
from models.enums import ModuleStatus

logger = logging.getLogger(__name__)


class InputWiringService:
    """
    Manages the wiring of module outputs to dependent module inputs.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_module_input_sources(self, library_module: ModuleLibrary) -> dict[str, list]:
        """
        Analyze a module's inputs and categorize by source.

        Args:
            library_module: ModuleLibrary object with inputs_metadata

        Returns:
            {
                "user": [{"name": "vpc_cidr", ...}],
                "module": [{"name": "vpc_id", "from_module": "infra/aws/vpc", "from_output": "vpc_id"}],
                "auto": [...]
            }
        """
        if not library_module or not library_module.inputs_metadata:
            return {"user": [], "module": [], "auto": []}

        inputs_metadata = library_module.inputs_metadata
        all_inputs = inputs_metadata.get("required", []) + inputs_metadata.get("optional", [])

        categorized = {"user": [], "module": [], "auto": []}
        for inp in all_inputs:
            source = inp.get("source", "user")
            if source in categorized:
                categorized[source].append(inp)
            else:
                categorized["user"].append(inp)

        return categorized

    def get_pending_inputs(self, project_module: ProjectModule) -> list[dict]:
        """
        Get list of inputs that are waiting for outputs from other modules.

        Args:
            project_module: ProjectModule to check

        Returns:
            List of:
                {
                    "name": "vpc_id",
                    "from_module": "infra/aws/vpc",
                    "from_output": "vpc_id",
                    "status": "pending|ready|missing|output_missing",
                    "value": <actual_value> or None
                }
        """
        if not project_module.library_module:
            return []

        input_sources = self.get_module_input_sources(project_module.library_module)
        module_inputs = input_sources.get("module", [])

        pending = []
        for inp in module_inputs:
            from_module_path = inp.get("from_module")
            from_output = inp.get("from_output")

            if not from_module_path or not from_output:
                logger.warning(f"Invalid module input definition: {inp}")
                continue

            # Find the source module in this project
            source_pm = self.db.query(ProjectModule).join(ModuleLibrary).filter(
                ProjectModule.project_id == project_module.project_id,
                ModuleLibrary.path == from_module_path
            ).first()

            status = "missing"  # Source module not in project
            value = None

            if source_pm:
                if source_pm.status == ModuleStatus.APPLIED and source_pm.outputs:
                    if from_output in source_pm.outputs:
                        status = "ready"
                        value = source_pm.outputs[from_output]
                    else:
                        status = "output_missing"
                else:
                    status = "pending"  # Module not yet applied

            pending.append({
                "name": inp.get("name"),
                "from_module": from_module_path,
                "from_output": from_output,
                "status": status,
                "value": value
            })

        return pending

    def are_all_inputs_ready(self, project_module: ProjectModule) -> tuple[bool, list]:
        """
        Check if all module inputs from other modules are satisfied.

        Args:
            project_module: ProjectModule to check

        Returns:
            Tuple of (all_ready: bool, not_ready: List)
        """
        pending = self.get_pending_inputs(project_module)
        not_ready = [p for p in pending if p["status"] != "ready"]
        return len(not_ready) == 0, not_ready

    def get_input_values_for_module(self, project_module: ProjectModule) -> dict[str, any]:
        """
        Get all ready input values for a module from other modules.

        Args:
            project_module: ProjectModule to get inputs for

        Returns:
            Dict of {input_name: value} for all ready inputs
        """
        pending = self.get_pending_inputs(project_module)
        ready_inputs = {p["name"]: p["value"] for p in pending if p["status"] == "ready"}
        return ready_inputs
