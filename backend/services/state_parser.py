"""
Terraform State Parser Service
Parses and analyzes terraform.tfstate files for visualization and inspection.

Supports:
- State format versions 3 and 4
- OpenTofu 1.8+ encrypted state files
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class EncryptedStateError(Exception):
    """Exception raised when trying to parse encrypted state without decryption."""
    pass


class TerraformStateParser:
    """
    Parser for Terraform state files (terraform.tfstate).
    Supports state format versions 3 and 4.
    Detects encrypted state (OpenTofu 1.8+) and provides appropriate handling.
    """

    def __init__(self, state_file_path: str, decrypted_state: dict | None = None):
        """
        Initialize the state parser.

        Args:
            state_file_path: Path to terraform.tfstate file
            decrypted_state: Optional pre-decrypted state data (for encrypted states)
        """
        self.state_file_path = Path(state_file_path)
        self.state_data: dict | None = decrypted_state
        self.version: int | None = None
        self.is_encrypted: bool = False
        self._raw_state: dict | None = None  # Original state (may be encrypted)

    def is_state_encrypted(self) -> bool:
        """Check if the state file is encrypted."""
        if self._raw_state is None:
            try:
                with open(self.state_file_path) as f:
                    self._raw_state = json.load(f)
            except Exception:
                return False
        return "encrypted_data" in self._raw_state

    def get_encrypted_metadata(self) -> dict[str, Any]:
        """
        Get metadata from encrypted state file.

        Metadata (lineage, serial) is NOT encrypted even when state encryption is enabled.

        Returns:
            Dictionary with available metadata
        """
        if self._raw_state is None:
            try:
                with open(self.state_file_path) as f:
                    self._raw_state = json.load(f)
            except Exception as e:
                return {"error": str(e)}

        return {
            "lineage": self._raw_state.get("lineage", "unknown"),
            "serial": self._raw_state.get("serial", 0),
            "encryption_version": self._raw_state.get("encryption_version", "unknown"),
            "is_encrypted": True,
            "terraform_version": "encrypted",  # Version is inside encrypted_data
        }

    def parse(self) -> dict[str, Any]:
        """
        Parse the Terraform state file.

        Returns:
            Dictionary containing parsed state information

        Raises:
            FileNotFoundError: If state file doesn't exist
            ValueError: If state file is invalid or malformed
            EncryptedStateError: If state is encrypted and no decrypted_state provided
        """
        if not self.state_file_path.exists():
            raise FileNotFoundError(f"State file not found: {self.state_file_path}")

        # If decrypted state was provided, use it
        if self.state_data is not None:
            self.is_encrypted = False
        else:
            try:
                with open(self.state_file_path) as f:
                    self.state_data = json.load(f)
                    self._raw_state = self.state_data
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON in state file: {e}")
            except Exception as e:
                raise ValueError(f"Failed to read state file: {e}")

        # Check if state is encrypted
        if "encrypted_data" in self.state_data:
            self.is_encrypted = True
            # Return encrypted state metadata
            return {
                'version': None,
                'terraform_version': 'encrypted',
                'serial': self.state_data.get('serial'),
                'lineage': self.state_data.get('lineage'),
                'is_encrypted': True,
                'encryption_version': self.state_data.get('encryption_version', 'unknown'),
                'resources': [],  # Cannot parse without decryption
                'outputs': {},
                'dependencies': [],
                'metadata': self.get_encrypted_metadata()
            }

        # Validate state structure
        if not isinstance(self.state_data, dict):
            raise ValueError("State file must be a JSON object")

        # Get state version
        self.version = self.state_data.get('version')
        if not self.version:
            raise ValueError("State file missing 'version' field")

        if self.version not in [3, 4]:
            logger.warning(f"Unsupported state version {self.version}, attempting to parse anyway")

        return {
            'version': self.version,
            'terraform_version': self.state_data.get('terraform_version'),
            'serial': self.state_data.get('serial'),
            'lineage': self.state_data.get('lineage'),
            'is_encrypted': False,
            'resources': self.extract_resources(),
            'outputs': self.extract_outputs(),
            'dependencies': self.extract_dependencies(),
            'metadata': self.extract_metadata()
        }

    def extract_resources(self) -> list[dict[str, Any]]:
        """
        Extract all resources from the state file.

        Returns:
            List of resource dictionaries with parsed information
        """
        if not self.state_data:
            return []

        resources = []

        # Version 4 format
        if self.version == 4:
            root_module = self.state_data.get('resources', [])
            resources.extend(self._parse_v4_resources(root_module))

        # Version 3 format
        elif self.version == 3:
            modules = self.state_data.get('modules', [])
            for module in modules:
                module_path = module.get('path', [])
                module_resources = module.get('resources', {})
                resources.extend(self._parse_v3_resources(module_resources, module_path))

        return resources

    def _parse_v4_resources(self, resources_list: list[dict]) -> list[dict[str, Any]]:
        """Parse resources from state format v4."""
        parsed_resources = []

        for resource in resources_list:
            try:
                # Clean provider name - handle both formats:
                # "provider[\"registry.terraform.io/hashicorp/aws\"]" or "registry.terraform.io/hashicorp/aws"
                provider_str = resource.get('provider', '')
                if 'provider[' in provider_str:
                    # Extract from provider["..."] format
                    provider_str = provider_str.split('"')[1] if '"' in provider_str else provider_str
                provider_name = provider_str.split('/')[-1].rstrip('"]')

                parsed_resource = {
                    'address': resource.get('name'),
                    'full_address': f"{resource.get('type')}.{resource.get('name')}",
                    'type': resource.get('type'),
                    'name': resource.get('name'),
                    'provider': provider_name,
                    'mode': resource.get('mode', 'managed'),  # managed or data
                    'instances': []
                }

                # Parse instances
                instances = resource.get('instances', [])
                for idx, instance in enumerate(instances):
                    instance_data = {
                        'index': idx,
                        'index_key': instance.get('index_key'),
                        'attributes': instance.get('attributes', {}),
                        'dependencies': instance.get('dependencies', []),
                        'schema_version': instance.get('schema_version'),
                        'private': instance.get('private'),
                    }

                    # Extract key attributes
                    attrs = instance.get('attributes', {})
                    instance_data['key_attributes'] = self._extract_key_attributes(
                        resource.get('type'), attrs
                    )

                    parsed_resource['instances'].append(instance_data)

                parsed_resources.append(parsed_resource)

            except Exception as e:
                logger.error(f"Error parsing resource: {e}")
                continue

        return parsed_resources

    def _parse_v3_resources(self, resources_dict: dict, module_path: list[str]) -> list[dict[str, Any]]:
        """Parse resources from state format v3."""
        parsed_resources = []

        for resource_key, resource_data in resources_dict.items():
            try:
                # Split resource key into type and name
                parts = resource_key.split('.', 1)
                resource_type = parts[0] if len(parts) > 0 else ''
                resource_name = parts[1] if len(parts) > 1 else ''

                # Clean provider name for v3 format - handle "provider.aws" format
                provider_str = resource_data.get('provider', '')
                provider_name = provider_str.split('.')[-1].rstrip('"]')

                parsed_resource = {
                    'address': resource_key,
                    'full_address': '.'.join(module_path + [resource_key]) if module_path else resource_key,
                    'type': resource_type,
                    'name': resource_name,
                    'provider': provider_name,
                    'mode': resource_data.get('type', 'managed'),
                    'module_path': module_path,
                    'instances': []
                }

                # V3 stores primary and depends_on at resource level
                primary = resource_data.get('primary', {})
                if primary:
                    instance_data = {
                        'index': 0,
                        'attributes': primary.get('attributes', {}),
                        'dependencies': resource_data.get('depends_on', []),
                        'tainted': primary.get('tainted', False),
                        'id': primary.get('id'),
                        'meta': primary.get('meta', {}),
                    }

                    instance_data['key_attributes'] = self._extract_key_attributes(
                        resource_type, primary.get('attributes', {})
                    )

                    parsed_resource['instances'].append(instance_data)

                parsed_resources.append(parsed_resource)

            except Exception as e:
                logger.error(f"Error parsing v3 resource {resource_key}: {e}")
                continue

        return parsed_resources

    def _extract_key_attributes(self, resource_type: str, attributes: dict) -> dict[str, Any]:
        """
        Extract important/commonly used attributes for display.

        Args:
            resource_type: Type of resource (e.g., aws_vpc, aws_subnet)
            attributes: Full attributes dictionary

        Returns:
            Dictionary of key attributes
        """
        key_attrs = {}

        # Common attributes across all resources
        common_keys = ['id', 'arn', 'name', 'tags', 'created_time', 'creation_date']
        for key in common_keys:
            if key in attributes:
                key_attrs[key] = attributes[key]

        # Resource-type specific attributes
        type_specific_attrs = {
            'aws_vpc': ['cidr_block', 'enable_dns_hostnames', 'enable_dns_support'],
            'aws_subnet': ['vpc_id', 'cidr_block', 'availability_zone'],
            'aws_instance': ['instance_type', 'ami', 'public_ip', 'private_ip'],
            'aws_security_group': ['vpc_id', 'description'],
            'aws_s3_bucket': ['bucket', 'region', 'versioning'],
            'aws_route_table': ['vpc_id'],
            'aws_internet_gateway': ['vpc_id'],
            'aws_nat_gateway': ['subnet_id', 'allocation_id'],
            'aws_lb': ['dns_name', 'load_balancer_type'],
            'aws_db_instance': ['engine', 'engine_version', 'instance_class', 'endpoint'],
            'aws_iam_role': ['arn', 'assume_role_policy'],
        }

        if resource_type in type_specific_attrs:
            for key in type_specific_attrs[resource_type]:
                if key in attributes:
                    key_attrs[key] = attributes[key]

        return key_attrs

    def extract_outputs(self) -> dict[str, Any]:
        """
        Extract outputs from the state file.

        Returns:
            Dictionary of output values
        """
        if not self.state_data:
            return {}

        outputs = {}

        # Version 4 format
        if self.version == 4:
            outputs_data = self.state_data.get('outputs', {})
            for output_name, output_data in outputs_data.items():
                outputs[output_name] = {
                    'value': output_data.get('value'),
                    'type': output_data.get('type'),
                    'sensitive': output_data.get('sensitive', False)
                }

        # Version 3 format
        elif self.version == 3:
            # V3 stores outputs in root module
            modules = self.state_data.get('modules', [])
            for module in modules:
                if module.get('path') == ['root']:
                    outputs_data = module.get('outputs', {})
                    for output_name, output_data in outputs_data.items():
                        outputs[output_name] = {
                            'value': output_data.get('value'),
                            'type': output_data.get('type'),
                            'sensitive': output_data.get('sensitive', False)
                        }
                    break

        return outputs

    def extract_dependencies(self) -> list[dict[str, Any]]:
        """
        Extract resource dependencies for graph visualization.

        Returns:
            List of dependency relationships (edges)
        """
        dependencies = []
        resources = self.extract_resources()

        for resource in resources:
            source = resource['full_address']

            for instance in resource.get('instances', []):
                deps = instance.get('dependencies', [])
                for dep in deps:
                    dependencies.append({
                        'source': source,
                        'target': dep,
                        'type': 'depends_on'
                    })

        return dependencies

    def extract_metadata(self) -> dict[str, Any]:
        """
        Extract metadata about the state file.

        Returns:
            Dictionary of metadata
        """
        if not self.state_data:
            return {}

        metadata = {
            'version': self.version,
            'terraform_version': self.state_data.get('terraform_version'),
            'serial': self.state_data.get('serial'),
            'lineage': self.state_data.get('lineage'),
            'resource_count': len(self.extract_resources()),
            'output_count': len(self.extract_outputs()),
        }

        # File metadata
        if self.state_file_path.exists():
            stat = self.state_file_path.stat()
            metadata['file_size'] = stat.st_size
            metadata['last_modified'] = datetime.fromtimestamp(stat.st_mtime).isoformat()

        return metadata

    def build_dependency_graph(self) -> dict[str, Any]:
        """
        Build a dependency graph structure for visualization.

        Returns:
            Graph structure with nodes and edges
        """
        resources = self.extract_resources()
        dependencies = self.extract_dependencies()

        # Build nodes
        nodes = []
        for resource in resources:
            node = {
                'id': resource['full_address'],
                'label': resource['name'],
                'type': resource['type'],
                'provider': resource['provider'],
                'mode': resource['mode'],
                'instance_count': len(resource.get('instances', []))
            }

            # Add key attributes for tooltip
            if resource.get('instances'):
                first_instance = resource['instances'][0]
                node['key_attributes'] = first_instance.get('key_attributes', {})

            nodes.append(node)

        # Build edges
        edges = []
        for dep in dependencies:
            edges.append({
                'from': dep['source'],
                'to': dep['target'],
                'type': dep['type']
            })

        return {
            'nodes': nodes,
            'edges': edges
        }

    def get_resource_by_address(self, address: str) -> dict[str, Any] | None:
        """
        Get a specific resource by its address.

        Args:
            address: Resource address (e.g., aws_vpc.main)

        Returns:
            Resource dictionary or None if not found
        """
        resources = self.extract_resources()
        for resource in resources:
            if resource['full_address'] == address or resource['address'] == address:
                return resource
        return None

    def get_resource_count_by_type(self) -> dict[str, int]:
        """
        Get count of resources grouped by type.

        Returns:
            Dictionary mapping resource type to count
        """
        resources = self.extract_resources()
        counts = {}

        for resource in resources:
            resource_type = resource['type']
            counts[resource_type] = counts.get(resource_type, 0) + 1

        return counts

    def get_provider_summary(self) -> dict[str, int]:
        """
        Get count of resources grouped by provider.

        Returns:
            Dictionary mapping provider to resource count
        """
        resources = self.extract_resources()
        counts = {}

        for resource in resources:
            provider = resource.get('provider', 'unknown')
            counts[provider] = counts.get(provider, 0) + 1

        return counts


class StateFileNotFoundError(Exception):
    """Exception raised when state file is not found."""
    pass


class StateFileInvalidError(Exception):
    """Exception raised when state file is invalid or malformed."""
    pass


# EncryptedStateError is defined at the top of this file


def parse_state_file(state_file_path: str) -> dict[str, Any]:
    """
    Convenience function to parse a Terraform state file.

    Args:
        state_file_path: Path to terraform.tfstate file

    Returns:
        Parsed state data

    Raises:
        StateFileNotFoundError: If state file doesn't exist
        StateFileInvalidError: If state file is invalid
    """
    try:
        parser = TerraformStateParser(state_file_path)
        return parser.parse()
    except FileNotFoundError as e:
        raise StateFileNotFoundError(str(e))
    except ValueError as e:
        raise StateFileInvalidError(str(e))


# ============================================================
# v1 function REMOVED in v2
# ============================================================
# get_state_file_path() - Used v1 clone_path pattern
# v2 state location: /app/state/{project_id}/{module_id}/terraform.tfstate
