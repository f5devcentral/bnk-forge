"""
Variable Parser Service
Parses Terraform/OpenTofu variable definitions from module source files
"""
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from services.git_auth_service import GitAuthContext, GitAuthService

logger = logging.getLogger(__name__)


class VariableParser:
    """Parse Terraform/OpenTofu variable definitions"""

    @staticmethod
    def parse_terraform_type(type_str: str) -> str:
        """
        Convert Terraform type to simplified form
        Examples:
        - string -> string
        - number -> number
        - bool -> bool
        - list(string) -> list
        - map(string) -> map
        - object({...}) -> object
        """
        if not type_str:
            return "any"

        type_str = type_str.strip()

        # Simple types
        if type_str in ["string", "number", "bool", "any"]:
            return type_str

        # List types
        if type_str.startswith("list"):
            return "list"

        # Map types
        if type_str.startswith("map"):
            return "map"

        # Set types
        if type_str.startswith("set"):
            return "set"

        # Object types
        if type_str.startswith("object"):
            return "object"

        # Tuple types
        if type_str.startswith("tuple"):
            return "tuple"

        return "any"

    @staticmethod
    def parse_default_value(default_str: str, var_type: str) -> Any:
        """
        Parse default value from HCL string
        """
        if not default_str or default_str.strip() == "":
            return None

        default_str = default_str.strip()

        # Handle null
        if default_str == "null":
            return None

        # Handle booleans
        if default_str in ["true", "false"]:
            return default_str == "true"

        # Handle numbers
        if var_type == "number":
            try:
                if "." in default_str:
                    return float(default_str)
                return int(default_str)
            except ValueError:
                return None

        # Handle strings (remove quotes)
        if var_type == "string":
            if default_str.startswith('"') and default_str.endswith('"'):
                return default_str[1:-1]
            if default_str.startswith("'") and default_str.endswith("'"):
                return default_str[1:-1]
            return default_str

        # Handle lists
        if var_type == "list":
            if default_str.startswith("[") and default_str.endswith("]"):
                # Simple list parsing
                content = default_str[1:-1].strip()
                if not content:
                    return []
                # Split by comma, handling quoted strings
                items = []
                current = ""
                in_quotes = False
                for char in content:
                    if char == '"' and (not current or current[-1] != '\\'):
                        in_quotes = not in_quotes
                    elif char == ',' and not in_quotes:
                        item = current.strip()
                        if item.startswith('"') and item.endswith('"'):
                            item = item[1:-1]
                        items.append(item)
                        current = ""
                        continue
                    current += char
                if current.strip():
                    item = current.strip()
                    if item.startswith('"') and item.endswith('"'):
                        item = item[1:-1]
                    items.append(item)
                return items
            return []

        # Handle maps/objects
        if var_type in ["map", "object"]:
            if default_str.startswith("{") and default_str.endswith("}"):
                # Return as string for now - complex parsing needed
                return default_str
            return {}

        return default_str

    @staticmethod
    def parse_variables_from_hcl(file_content: str) -> list[dict[str, Any]]:
        """
        Parse variable definitions from Terraform/HCL file content

        Returns list of variable definitions with schema:
        {
            "name": "vpc_cidr",
            "type": "string",
            "description": "CIDR block for VPC",
            "default": "10.0.0.0/16",
            "required": False
        }
        """
        variables = []

        # Pattern to match variable blocks
        # variable "name" {
        #   type = string
        #   description = "..."
        #   default = ...
        # }
        variable_pattern = r'variable\s+"([^"]+)"\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}'

        matches = re.finditer(variable_pattern, file_content, re.MULTILINE | re.DOTALL)

        for match in matches:
            var_name = match.group(1)
            var_block = match.group(2)

            # Extract type
            type_match = re.search(r'type\s*=\s*([^\n]+)', var_block)
            var_type = "any"
            if type_match:
                type_str = type_match.group(1).strip()
                var_type = VariableParser.parse_terraform_type(type_str)

            # Extract description
            desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
            description = desc_match.group(1) if desc_match else ""

            # Extract default value
            default_match = re.search(r'default\s*=\s*([^\n]+)', var_block)
            default_value = None
            required = True

            if default_match:
                default_str = default_match.group(1).strip()
                # Remove trailing comment
                if '#' in default_str:
                    default_str = default_str.split('#')[0].strip()
                default_value = VariableParser.parse_default_value(default_str, var_type)
                required = False

            # Extract validation rules (optional)
            validation_blocks = re.findall(r'validation\s*\{([^}]+)\}', var_block)
            validations = []
            for val_block in validation_blocks:
                condition_match = re.search(r'condition\s*=\s*([^\n]+)', val_block)
                error_match = re.search(r'error_message\s*=\s*"([^"]*)"', val_block)
                if condition_match:
                    validations.append({
                        "condition": condition_match.group(1).strip(),
                        "error_message": error_match.group(1) if error_match else ""
                    })

            # Extract sensitive flag
            sensitive_match = re.search(r'sensitive\s*=\s*(true|false)', var_block)
            sensitive = sensitive_match.group(1) == "true" if sensitive_match else False

            variable = {
                "name": var_name,
                "type": var_type,
                "description": description,
                "default": default_value,
                "required": required,
                "sensitive": sensitive
            }

            if validations:
                variable["validations"] = validations

            variables.append(variable)

        return variables

    @staticmethod
    def detect_dependencies_from_variables(variables: list[dict[str, Any]], module_name: str = None) -> list[str]:
        """
        Detect module dependencies by analyzing variable names and descriptions

        Looks for patterns like:
        - Variable names: vpc_id, subnet_ids, eks_cluster_name, security_group_id
        - Descriptions mentioning: "from VPC module", "EKS cluster", etc.

        Args:
            variables: List of variable definitions
            module_name: Name of the current module (to filter out self-dependencies)

        Returns list of inferred module names (e.g., ["vpc", "eks"])
        """
        dependencies = set()

        # Normalize module name for comparison
        module_name_lower = module_name.lower() if module_name else None

        # Common module name patterns with specific suffixes that indicate cross-module references
        # Only match when variable clearly references another module's output
        module_patterns = {
            'vpc': ['vpc'],
            'eks': ['eks', 'cluster'],
            'subnet': ['subnet'],
            'security': ['security_group', 'sg'],
            'storage': ['ebs', 's3', 'efs'],
            'high-performance-nodes': ['nodegroup'],
            'database': ['rds', 'db_instance'],
            'cert-manager': ['certificate'],
            'network-setup': ['network'],
        }

        # Suffixes that indicate a cross-module reference (not internal configuration)
        cross_module_suffixes = ['_id', '_ids', '_arn', '_arns', '_name', '_endpoint', '_url', '_dns']

        for var in variables:
            var_name = var.get('name', '').lower()
            var.get('description', '').lower()

            # Check variable name patterns - must have a cross-module suffix
            for module_name, patterns in module_patterns.items():
                for pattern in patterns:
                    # Only match if variable name contains both the pattern AND a cross-module suffix
                    # This avoids self-references like "storage_class" or "node_count"
                    if pattern in var_name:
                        # Check if it has a cross-module suffix after the pattern
                        for suffix in cross_module_suffixes:
                            if suffix in var_name:
                                # Make sure pattern comes before suffix (vpc_id, not id_vpc)
                                pattern_pos = var_name.find(pattern)
                                suffix_pos = var_name.find(suffix)
                                if pattern_pos < suffix_pos:
                                    # Additional check: the suffix should be immediately after the pattern
                                    # or with minimal separation (like vpc_id, subnet_ids, eks_cluster_name)
                                    between = var_name[pattern_pos + len(pattern):suffix_pos]
                                    # Allow connectors like _, cluster, but not long unrelated text
                                    if len(between) <= 15:  # Reasonable variable name component
                                        dependencies.add(module_name)
                                    break
                        break

                    # REMOVED: Description-based matching is too prone to false positives
                    # Dependencies should ONLY be inferred from explicit variable references

        # Filter out self-dependencies
        if module_name_lower:
            dependencies.discard(module_name_lower)

        return sorted(list(dependencies))

    @staticmethod
    def clone_and_parse_git_module(
        git_source: str,
        module_path: str = "",
        git_token: str | None = None,
        auth_context: GitAuthContext | None = None,
    ) -> tuple[bool, list[dict[str, Any]], str]:
        """
        Clone a git module temporarily and parse its variables

        Args:
            git_source: Git source URL (can include ref)
            module_path: Path within the repository
            git_token: Optional Git PAT/token for authentication

        Returns:
            (success, variables_list, error_message)
        """
        temp_dir = None
        try:
            # Parse git source
            # Format: git::https://github.com/user/repo.git//path?ref=version
            # or: https://github.com/user/repo.git

            git_url = git_source
            git_ref = None

            # Remove git:: prefix if present
            if git_source.startswith("git::"):
                git_url = git_source[5:]

            # Security check: Prevent argument injection
            if git_url.startswith("-"):
                return False, [], "Invalid git source: cannot start with '-'"

            # Extract ref if present
            if "?ref=" in git_url:
                git_url, ref_part = git_url.split("?ref=")
                git_ref = ref_part

            # Extract path if present (after //)
            if "//" in git_url:
                # Find the // that's not part of the protocol
                parts = git_url.split("://", 1)
                if len(parts) == 2:
                    protocol, rest = parts
                    if "//" in rest:
                        url_part, path_part = rest.split("//", 1)
                        git_url = f"{protocol}://{url_part}"
                        if not module_path:
                            module_path = path_part

            git_url = GitAuthService.strip_url_credentials(git_url)

            # Backward-compatible fallback for existing callers that still pass git_token.
            auth_ctx = auth_context
            if auth_ctx is None and git_token:
                auth_ctx = GitAuthContext(
                    transport="https_token",
                    credential_type="legacy_pat",
                    secret=git_token,
                    username="git",
                )
            if auth_ctx is None:
                auth_ctx = GitAuthContext(transport="none", credential_type="none")

            # Create temp directory
            temp_dir = tempfile.mkdtemp(prefix="bnk_forge_var_parse_")
            env, cleanup_env = GitAuthService.build_git_environment(auth_ctx)

            # Clone repository
            clone_cmd = ["git", "clone", "--depth", "1"]
            if git_ref:
                clone_cmd.extend(["--branch", git_ref])

            # Use -- to prevent argument injection
            clone_cmd.extend(["--", git_url, temp_dir])

            result = subprocess.run(
                clone_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                error_kind, guidance = GitAuthService.classify_git_failure(result.stderr)
                sanitized = GitAuthService.sanitize_error_text(result.stderr, secrets=[auth_ctx.secret])
                return False, [], f"Failed to clone repository [{error_kind}] {guidance}: {sanitized}"

            # Find variables.tf or terragrunt.hcl
            search_dir = os.path.join(temp_dir, module_path) if module_path else temp_dir

            if not os.path.exists(search_dir):
                return False, [], f"Module path not found: {module_path}"

            variables = []

            # Look for variables.tf first
            variables_file = os.path.join(search_dir, "variables.tf")
            if os.path.exists(variables_file):
                with open(variables_file) as f:
                    content = f.read()
                    variables = VariableParser.parse_variables_from_hcl(content)

            # Also check main.tf and other .tf files
            if not variables:
                for tf_file in ["main.tf", "vars.tf"]:
                    tf_path = os.path.join(search_dir, tf_file)
                    if os.path.exists(tf_path):
                        with open(tf_path) as f:
                            content = f.read()
                            file_vars = VariableParser.parse_variables_from_hcl(content)
                            variables.extend(file_vars)

            # v1 Terragrunt code removed - v2 doesn't use terragrunt.hcl files
            # terragrunt_file = os.path.join(search_dir, "terragrunt.hcl")
            # if os.path.exists(terragrunt_file):
            #     with open(terragrunt_file, 'r') as f:
            #         content = f.read()
            #         # Parse inputs block if present
            #         inputs_match = re.search(r'inputs\s*=\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}', content, re.DOTALL)
            #         if inputs_match:
            #             # This shows example inputs, we can use it to enhance variable info
            #             pass

            return True, variables, ""

        except subprocess.TimeoutExpired:
            return False, [], "Git clone timed out"
        except Exception as e:
            sanitized = GitAuthService.sanitize_error_text(str(e), secrets=[auth_context.secret if auth_context else git_token])
            logger.error(f"Error parsing git module: {sanitized}")
            return False, [], sanitized
        finally:
            if 'cleanup_env' in locals():
                cleanup_env()
            # Clean up temp directory
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception as e:
                    logger.warning(f"Failed to clean up temp directory: {e}")


class VariableValidator:
    """Validate variable values against schema"""

    @staticmethod
    def validate_variable_value(value: Any, var_schema: dict[str, Any]) -> tuple[bool, str]:
        """
        Validate a variable value against its schema

        Args:
            value: The value to validate
            var_schema: Variable schema dict with type, required, etc.

        Returns:
            (is_valid, error_message)
        """
        var_name = var_schema.get("name", "unknown")
        var_type = var_schema.get("type", "any")
        required = var_schema.get("required", True)

        # Check if required
        if required and (value is None or value == ""):
            return False, f"Variable '{var_name}' is required"

        # If not required and value is None, it's valid
        if not required and (value is None or value == ""):
            return True, ""

        # Type validation
        if var_type == "string":
            if not isinstance(value, str):
                return False, f"Variable '{var_name}' must be a string"

        elif var_type == "number":
            if not isinstance(value, (int, float)):
                try:
                    float(value)
                except (ValueError, TypeError):
                    return False, f"Variable '{var_name}' must be a number"

        elif var_type == "bool":
            if not isinstance(value, bool):
                if str(value).lower() not in ["true", "false", "0", "1"]:
                    return False, f"Variable '{var_name}' must be a boolean"

        elif var_type == "list":
            if not isinstance(value, list):
                return False, f"Variable '{var_name}' must be a list"

        elif var_type == "map":
            if not isinstance(value, dict):
                return False, f"Variable '{var_name}' must be a map/object"

        elif var_type == "object":
            if not isinstance(value, dict):
                return False, f"Variable '{var_name}' must be an object"

        # Additional validations can be added here (min/max, regex, etc.)

        return True, ""

    @staticmethod
    def validate_variables(
        variable_values: dict[str, Any],
        variables_schema: list[dict[str, Any]]
    ) -> tuple[bool, list[str]]:
        """
        Validate a set of variable values against schema

        Args:
            variable_values: Dict of variable names to values
            variables_schema: List of variable schema dicts

        Returns:
            (all_valid, list_of_errors)
        """
        errors = []

        # Create schema lookup
        schema_map = {v["name"]: v for v in variables_schema}

        # Check all required variables are provided
        for var_schema in variables_schema:
            var_name = var_schema["name"]
            required = var_schema.get("required", True)

            if required and var_name not in variable_values:
                errors.append(f"Required variable '{var_name}' is missing")

        # Validate provided values
        for var_name, value in variable_values.items():
            if var_name not in schema_map:
                # Unknown variable - could warn but not error
                continue

            var_schema = schema_map[var_name]
            is_valid, error_msg = VariableValidator.validate_variable_value(value, var_schema)

            if not is_valid:
                errors.append(error_msg)

        return len(errors) == 0, errors

    @staticmethod
    def validate_user_variables(
        variable_values: dict[str, Any],
        inputs_metadata: dict[str, list[dict[str, Any]]]
    ) -> tuple[bool, list[str]]:
        """
        Validate user-provided variables for v2 modules (with inputs_metadata).
        Only validates variables with source="user" or source="project".
        Skips validation for variables with source="module" (wired from dependencies).

        Args:
            variable_values: Dict of variable names to values provided by user
            inputs_metadata: Module's inputs_metadata (has 'required' and 'optional' keys)

        Returns:
            (all_valid, list_of_errors)
        """
        errors = []

        # Combine required and optional inputs
        all_inputs = (inputs_metadata.get("required", []) +
                     inputs_metadata.get("optional", []))

        # Create lookup map
        inputs_map = {inp["name"]: inp for inp in all_inputs}

        # Check all required USER/PROJECT inputs are provided
        for inp in inputs_metadata.get("required", []):
            var_name = inp["name"]
            source = inp.get("source", "user")

            # Skip module-sourced variables (wired from dependencies)
            if source == "module":
                continue

            # Check if user provided this variable
            if var_name not in variable_values:
                # If source is "project", it's auto-filled, so don't require it
                if source != "project":
                    errors.append(f"Required variable '{var_name}' is missing")

        # Validate provided values
        for var_name, value in variable_values.items():
            if var_name not in inputs_map:
                # Unknown variable - warn but don't error
                continue

            inp = inputs_map[var_name]
            var_type = inp.get("type", "string")

            # Simple type validation
            if var_type == "string" and not isinstance(value, str):
                errors.append(f"Variable '{var_name}' must be a string")
            elif var_type in ["number", "int", "integer"] and not isinstance(value, (int, float)):
                errors.append(f"Variable '{var_name}' must be a number")
            elif var_type in ["bool", "boolean"] and not isinstance(value, bool):
                # Accept boolean-like strings (forms/blueprint ${...} deliver
                # "true"/"false"); consistent with validate_variable_value() and
                # the container engine's when-gating, which both coerce strings.
                if str(value).strip().lower() not in ("true", "false", "0", "1"):
                    errors.append(f"Variable '{var_name}' must be a boolean")
            elif "list" in var_type and not isinstance(value, list):
                errors.append(f"Variable '{var_name}' must be a list")
            elif "map" in var_type or "object" in var_type:
                if not isinstance(value, dict):
                    errors.append(f"Variable '{var_name}' must be a map/object")

        return len(errors) == 0, errors
