"""
User Module Management Service
Handles validation, metadata extraction, and version management for user-added modules
"""
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from models import ModuleLibrary
from models.enums import ModuleTestStatus

logger = logging.getLogger(__name__)


class UserModuleService:
    """Service for managing user-added OpenTofu modules"""

    def __init__(self, db: Session):
        self.db = db
        self.temp_dir = os.path.join("/tmp", "bnk-forge-user-modules")
        os.makedirs(self.temp_dir, exist_ok=True)

    def validate_git_source(self, git_url: str, git_ref: str = "main") -> dict:
        """
        Validate that a git source is accessible and contains Terraform code

        Args:
            git_url: Git repository URL
            git_ref: Git reference (branch/tag)

        Returns:
            Dict with validation results:
            {
                'valid': bool,
                'error': str (if not valid),
                'available_refs': List[str],
                'has_terraform_files': bool,
                'terraform_files': List[str]
            }
        """
        import git  # noqa: E402 — lazy: git CLI not in slim API container

        result = {
            'valid': False,
            'error': None,
            'available_refs': [],
            'has_terraform_files': False,
            'terraform_files': []
        }

        temp_path = None
        try:
            # Create temporary directory for cloning
            temp_path = tempfile.mkdtemp(dir=self.temp_dir)
            logger.info(f"Validating git source: {git_url} (ref: {git_ref})")

            # Clone repository
            try:
                repo = git.Repo.clone_from(git_url, temp_path, depth=1, branch=git_ref)
            except git.exc.GitCommandError:
                # Try cloning without specific branch first
                try:
                    repo = git.Repo.clone_from(git_url, temp_path, depth=1)
                except git.exc.GitCommandError as clone_error:
                    result['error'] = f"Failed to clone repository: {str(clone_error)}"
                    return result

            # Get available branches and tags
            try:
                # Fetch all refs
                origin = repo.remotes.origin
                origin.fetch()

                # Get branches
                branches = [ref.name.replace('origin/', '') for ref in origin.refs
                           if not ref.name.endswith('HEAD')]
                result['available_refs'] = sorted(branches)
            except Exception as e:
                logger.warning(f"Failed to get refs: {e}")
                result['available_refs'] = [git_ref]

            # Check for Terraform files
            terraform_files = []
            for root, dirs, files in os.walk(temp_path):
                # Skip .git directory
                if '.git' in root:
                    continue

                for file in files:
                    if file.endswith(('.tf', '.hcl')):
                        rel_path = os.path.relpath(os.path.join(root, file), temp_path)
                        terraform_files.append(rel_path)

            result['terraform_files'] = terraform_files
            result['has_terraform_files'] = len(terraform_files) > 0

            if not result['has_terraform_files']:
                result['error'] = "No Terraform files (.tf, .hcl) found in repository"
                return result

            # Check for main.tf at root or subdirectories (primary check for OpenTofu modules)
            has_main_tf = any('main.tf' in f for f in terraform_files)

            if not has_main_tf:
                result['error'] = "No main.tf found in repository - must be a valid OpenTofu/Terraform module"
                return result

            result['valid'] = True
            logger.info(f"Git source validated successfully: {len(terraform_files)} Terraform files found")

        except Exception as e:
            result['error'] = f"Validation failed: {str(e)}"
            logger.error(f"Git source validation error: {e}")

        finally:
            # Cleanup temp directory
            if temp_path and os.path.exists(temp_path):
                try:
                    shutil.rmtree(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp directory: {e}")

        return result

    def extract_module_metadata(self, git_url: str, git_ref: str = "main",
                                module_path: str = "") -> dict:
        """
        Extract metadata from Terraform module by parsing files

        Args:
            git_url: Git repository URL
            git_ref: Git reference (branch/tag)
            module_path: Path within repository to module (e.g., 'modules/vpc')

        Returns:
            Dict with extracted metadata:
            {
                'success': bool,
                'error': str,
                'variables_schema': Dict,
                'outputs_schema': Dict,
                'dependencies': List[str],
                'provider': str,
                'description': str,
                'readme': str,
                'terraform_version': str
            }
        """
        result = {
            'success': False,
            'error': None,
            'variables_schema': {},
            'outputs_schema': {},
            'dependencies': [],
            'provider': None,
            'description': '',
            'readme': '',
            'terraform_version': None
        }

        import git  # noqa: E402 — lazy: git CLI not in slim API container

        temp_path = None
        try:
            # Clone repository
            temp_path = tempfile.mkdtemp(dir=self.temp_dir)
            logger.info(f"Extracting metadata from: {git_url} (ref: {git_ref})")

            try:
                git.Repo.clone_from(git_url, temp_path, depth=1, branch=git_ref)
            except git.exc.GitCommandError:
                # Try without branch
                git.Repo.clone_from(git_url, temp_path, depth=1)

            # Determine module directory
            module_dir = os.path.join(temp_path, module_path) if module_path else temp_path

            if not os.path.exists(module_dir):
                result['error'] = f"Module path '{module_path}' not found in repository"
                return result

            # Parse variables.tf
            variables_tf = os.path.join(module_dir, 'variables.tf')
            if os.path.exists(variables_tf):
                result['variables_schema'] = self._parse_variables_file(variables_tf)

            # Parse outputs.tf
            outputs_tf = os.path.join(module_dir, 'outputs.tf')
            if os.path.exists(outputs_tf):
                result['outputs_schema'] = self._parse_outputs_file(outputs_tf)

            # Parse main.tf for dependencies and provider
            main_tf = os.path.join(module_dir, 'main.tf')
            if os.path.exists(main_tf):
                deps, provider = self._parse_main_file(main_tf)
                result['dependencies'] = deps
                result['provider'] = provider

            # Parse versions.tf for Terraform version requirements
            versions_tf = os.path.join(module_dir, 'versions.tf')
            if os.path.exists(versions_tf):
                tf_version, provider_info = self._parse_versions_file(versions_tf)
                result['terraform_version'] = tf_version
                if not result['provider'] and provider_info:
                    result['provider'] = provider_info

            # Read README.md
            readme_path = os.path.join(module_dir, 'README.md')
            if os.path.exists(readme_path):
                with open(readme_path, encoding='utf-8', errors='ignore') as f:
                    readme_content = f.read()
                    result['readme'] = readme_content
                    result['description'] = self._extract_description_from_readme(readme_content)

            result['success'] = True
            logger.info(f"Metadata extracted: {len(result['variables_schema'])} variables, "
                       f"{len(result['dependencies'])} dependencies")

        except Exception as e:
            result['error'] = f"Metadata extraction failed: {str(e)}"
            logger.error(f"Metadata extraction error: {e}")

        finally:
            # Cleanup
            if temp_path and os.path.exists(temp_path):
                try:
                    shutil.rmtree(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp directory: {e}")

        return result

    def _parse_variables_file(self, file_path: str) -> dict:
        """Parse variables.tf to extract variable definitions"""
        variables = {}

        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Simple regex-based parsing for variable blocks
            # Format: variable "name" { ... }
            variable_pattern = r'variable\s+"([^"]+)"\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}'
            matches = re.finditer(variable_pattern, content, re.DOTALL)

            for match in matches:
                var_name = match.group(1)
                var_block = match.group(2)

                var_def = {
                    'type': 'string',  # Default
                    'required': True,   # Default
                    'description': '',
                    'default': None
                }

                # Extract type
                type_match = re.search(r'type\s*=\s*([^\n]+)', var_block)
                if type_match:
                    var_def['type'] = type_match.group(1).strip()

                # Extract description
                desc_match = re.search(r'description\s*=\s*"([^"]*)"', var_block)
                if desc_match:
                    var_def['description'] = desc_match.group(1)

                # Extract default value
                default_match = re.search(r'default\s*=\s*([^\n]+)', var_block)
                if default_match:
                    default_value = default_match.group(1).strip()
                    var_def['default'] = default_value
                    var_def['required'] = False

                variables[var_name] = var_def

        except Exception as e:
            logger.warning(f"Failed to parse variables.tf: {e}")

        return variables

    def _parse_outputs_file(self, file_path: str) -> dict:
        """Parse outputs.tf to extract output definitions"""
        outputs = {}

        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Simple regex for output blocks
            output_pattern = r'output\s+"([^"]+)"\s*\{([^}]*)\}'
            matches = re.finditer(output_pattern, content, re.DOTALL)

            for match in matches:
                output_name = match.group(1)
                output_block = match.group(2)

                output_def = {
                    'description': '',
                    'sensitive': False
                }

                # Extract description
                desc_match = re.search(r'description\s*=\s*"([^"]*)"', output_block)
                if desc_match:
                    output_def['description'] = desc_match.group(1)

                # Check if sensitive
                if 'sensitive = true' in output_block.lower():
                    output_def['sensitive'] = True

                outputs[output_name] = output_def

        except Exception as e:
            logger.warning(f"Failed to parse outputs.tf: {e}")

        return outputs

    def _parse_main_file(self, file_path: str) -> tuple[list[str], str | None]:
        """Parse main.tf to extract module dependencies and provider"""
        dependencies = []
        provider = None

        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Find module blocks to identify dependencies
            module_pattern = r'module\s+"([^"]+)"\s*\{[^}]*source\s*=\s*"([^"]+)"'
            matches = re.finditer(module_pattern, content, re.DOTALL)

            for match in matches:
                module_name = match.group(1)
                module_source = match.group(2)
                # Extract module name from source if it's a git URL
                if 'git::' in module_source or '.git' in module_source:
                    # Try to get module name from path
                    parts = module_source.split('/')
                    if len(parts) > 0:
                        dependencies.append(parts[-1].replace('.git', ''))
                else:
                    dependencies.append(module_name)

            # Detect provider from resource blocks
            # Look for resource "provider_resource_type" patterns
            if 'resource "aws_' in content:
                provider = 'aws'
            elif 'resource "azurerm_' in content or 'resource "azure_' in content:
                provider = 'azure'
            elif 'resource "google_' in content:
                provider = 'gcp'
            elif 'resource "kubernetes_' in content or 'resource "helm_' in content:
                provider = 'kubernetes'

        except Exception as e:
            logger.warning(f"Failed to parse main.tf: {e}")

        return dependencies, provider

    def _parse_versions_file(self, file_path: str) -> tuple[str | None, str | None]:
        """Parse versions.tf to extract Terraform version and provider requirements"""
        tf_version = None
        provider = None

        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Extract required Terraform version
            tf_ver_match = re.search(r'required_version\s*=\s*"([^"]+)"', content)
            if tf_ver_match:
                tf_version = tf_ver_match.group(1)

            # Extract required providers
            if not provider:
                if 'source = "hashicorp/aws"' in content:
                    provider = 'aws'
                elif 'source = "hashicorp/azurerm"' in content:
                    provider = 'azure'
                elif 'source = "hashicorp/google"' in content:
                    provider = 'gcp'
                elif 'source = "hashicorp/kubernetes"' in content:
                    provider = 'kubernetes'

        except Exception as e:
            logger.warning(f"Failed to parse versions.tf: {e}")

        return tf_version, provider

    def _extract_description_from_readme(self, readme_content: str) -> str:
        """Extract description from README.md"""
        lines = readme_content.split('\n')
        description_lines = []

        in_description = False
        for line in lines:
            # Look for ## Description section
            if line.strip().startswith('## Description'):
                in_description = True
                continue

            if in_description:
                # Stop at next heading
                if line.strip().startswith('##'):
                    break
                if line.strip():
                    description_lines.append(line.strip())

        if description_lines:
            return ' '.join(description_lines)

        # Fallback: use first paragraph after title
        for i, line in enumerate(lines):
            if line.startswith('# ') and i + 1 < len(lines):
                # Skip title, get next non-empty line
                for j in range(i + 1, len(lines)):
                    if lines[j].strip() and not lines[j].startswith('#'):
                        return lines[j].strip()

        return ''

    def list_module_versions(self, git_url: str) -> dict:
        """
        List available versions (tags and branches) for a git repository

        Args:
            git_url: Git repository URL

        Returns:
            Dict with branches and tags lists
        """
        result = {
            'success': False,
            'error': None,
            'branches': [],
            'tags': []
        }

        import git  # noqa: E402 — lazy: git CLI not in slim API container

        temp_path = None
        try:
            temp_path = tempfile.mkdtemp(dir=self.temp_dir)
            logger.info(f"Listing versions for: {git_url}")

            # Clone repository (shallow clone)
            repo = git.Repo.clone_from(git_url, temp_path, depth=1)

            # Fetch all refs
            origin = repo.remotes.origin
            origin.fetch()

            # Get branches
            branches = []
            for ref in origin.refs:
                if not ref.name.endswith('HEAD'):
                    branch_name = ref.name.replace('origin/', '')
                    branches.append(branch_name)

            # Get tags
            tags = [tag.name for tag in repo.tags]

            result['branches'] = sorted(branches)
            result['tags'] = sorted(tags, reverse=True)  # Latest first
            result['success'] = True

            logger.info(f"Found {len(branches)} branches and {len(tags)} tags")

        except Exception as e:
            result['error'] = f"Failed to list versions: {str(e)}"
            logger.error(f"Version listing error: {e}")

        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    shutil.rmtree(temp_path)
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp directory: {e}")

        return result

    def create_user_module(
        self,
        name: str,
        category: str,
        git_url: str,
        git_ref: str = "main",
        module_path: str = "",
        provider: str | None = None,
        description: str | None = None,
        auto_extract_metadata: bool = True
    ) -> dict:
        """
        Create a new user module with validation and metadata extraction

        Args:
            name: Module name
            category: Module category (infra, k8s, bnk)
            git_url: Git repository URL
            git_ref: Git reference (branch/tag)
            module_path: Path within repository to module
            provider: Cloud provider (optional, auto-detected if not provided)
            description: Module description (optional, extracted from README if not provided)
            auto_extract_metadata: Automatically extract metadata from Terraform files

        Returns:
            Dict with created module info or error
        """
        result = {
            'success': False,
            'error': None,
            'module': None
        }

        try:
            # Step 1: Validate git source
            logger.info(f"Creating user module '{name}' from {git_url}")
            validation = self.validate_git_source(git_url, git_ref)

            if not validation['valid']:
                result['error'] = validation['error']
                return result

            # Step 2: Extract metadata if requested
            metadata = {}
            if auto_extract_metadata:
                metadata = self.extract_module_metadata(git_url, git_ref, module_path)

                if not metadata['success']:
                    logger.warning(f"Metadata extraction failed: {metadata['error']}")
                    # Continue anyway, just with empty metadata

            # Step 3: Build git source string
            git_source_path = f"{module_path}" if module_path else ""
            git_source = f"git::{git_url}//{git_source_path}?ref={git_ref}"

            # Step 4: Create module in database
            module_data = {
                'name': name,
                'category': category,
                'git_source': git_source,
                'version': git_ref,
                'module_source_kind': 'user_module',
                'execution_engine': 'opentofu',
                'deploy_model': 'tofu_module',
                'engine_type': 'opentofu',
                'provider': provider or metadata.get('provider'),
                'description': description or metadata.get('description', f'User module: {name}'),
                'readme': metadata.get('readme', ''),
                'variables_schema': metadata.get('variables_schema', {}),
                'dependencies': metadata.get('dependencies', []),
                'workflow_compatibility': ['greenfield', 'partial', 'minimal'],  # User modules support all
                'is_official': False,
                'is_tested': False,
                'test_status': ModuleTestStatus.NOT_TESTED,
                'is_active': True,
                'path': module_path
            }

            # Check if module with same git_source already exists
            existing = self.db.query(ModuleLibrary).filter(
                ModuleLibrary.git_source == git_source
            ).first()

            if existing:
                result['error'] = f"Module with same git source already exists: {existing.name}"
                return result

            module = ModuleLibrary(**module_data)
            module.last_synced = datetime.now(UTC)

            self.db.add(module)
            self.db.flush()
            self.db.refresh(module)

            result['success'] = True
            result['module'] = {
                'id': module.id,
                'name': module.name,
                'category': module.category,
                'provider': module.provider,
                'description': module.description,
                'git_source': module.git_source,
                'version': module.version,
                'variables_count': len(module.variables_schema or {}),
                'dependencies_count': len(module.dependencies or [])
            }

            logger.info(f"User module created successfully: {name} (ID: {module.id})")

        except Exception as e:
            result['error'] = f"Failed to create module: {str(e)}"
            logger.error(f"Module creation error: {e}")

        return result

    def update_module_version(self, module_id: int, new_version: str) -> dict:
        """
        Update a user module to a different version and re-extract metadata

        Args:
            module_id: Module ID
            new_version: New git reference (branch/tag)

        Returns:
            Dict with success status
        """
        result = {
            'success': False,
            'error': None
        }

        try:
            module = self.db.query(ModuleLibrary).filter(
                ModuleLibrary.id == module_id
            ).first()

            if not module:
                result['error'] = "Module not found"
                return result

            if module.is_official:
                result['error'] = "Cannot update official modules"
                return result

            # Extract git URL from git_source
            # Format: git::https://github.com/user/repo.git//path?ref=version
            # Remove 'git::' prefix first
            source_without_prefix = module.git_source.replace('git::', '', 1)

            # Now format is: https://github.com/user/repo.git//path?ref=version or https://github.com/user/repo.git?ref=version
            # Find the path separator '//' that's NOT part of the protocol
            parts = source_without_prefix.split('://', 1)  # Split protocol
            module_path = ''
            if len(parts) == 2:
                protocol, rest = parts  # protocol='https', rest='github.com/user/repo.git//path?ref=version'
                if '//' in rest:
                    # Has path separator
                    url_parts = rest.split('//', 1)
                    git_url = f"{protocol}://{url_parts[0]}"  # https://github.com/user/repo.git
                    module_path = url_parts[1].split('?')[0]  # path before ?ref=
                else:
                    # No path separator
                    git_url = f"{protocol}://{rest.split('?')[0]}"  # https://github.com/user/repo.git
            else:
                # Fallback
                git_url = source_without_prefix.split('?')[0]

            # Validate new version exists
            validation = self.validate_git_source(git_url, new_version)
            if not validation['valid']:
                result['error'] = validation['error']
                return result

            # Extract metadata for new version
            metadata = self.extract_module_metadata(git_url, new_version, module_path)

            # Update module
            module.version = new_version
            module.git_source = f"git::{git_url}//{module_path}?ref={new_version}"

            if metadata['success']:
                if metadata.get('variables_schema'):
                    module.variables_schema = metadata['variables_schema']
                if metadata.get('dependencies'):
                    module.dependencies = metadata['dependencies']
                if metadata.get('provider'):
                    module.provider = metadata['provider']
                if metadata.get('description'):
                    module.description = metadata['description']
                if metadata.get('readme'):
                    module.readme = metadata['readme']

            module.last_synced = datetime.now(UTC)
            module.test_status = ModuleTestStatus.NOT_TESTED  # Reset test status
            module.is_tested = False

            result['success'] = True
            logger.info(f"Module {module_id} updated to version {new_version}")

        except Exception as e:
            result['error'] = f"Failed to update module version: {str(e)}"
            logger.error(f"Version update error: {e}")

        return result
