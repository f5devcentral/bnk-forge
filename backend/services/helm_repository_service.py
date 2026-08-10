"""
Helm repository management mixin.

Extracted from helm_service.py (R4-012) to keep the monolith under control.
Provides repository CRUD and chart browsing/search capabilities.
"""

import json
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class HelmRepositoryMixin:
    """
    Mixin providing Helm repository management methods.

    Expects the host class to provide:
        - self._validate_arg(name, value)
    """

    def add_repository(
        self,
        name: str,
        url: str,
        username: str | None = None,
        password: str | None = None
    ) -> dict[str, Any]:
        """
        Add a Helm chart repository.

        Args:
            name: Repository name
            url: Repository URL
            username: Optional username for authentication
            password: Optional password for authentication

        Returns:
            Result dictionary
        """
        self._validate_arg("name", name)
        self._validate_arg("url", url)
        self._validate_arg("username", username)
        self._validate_arg("password", password)

        command = ['helm', 'repo', 'add', name, url]

        if username and password:
            command.extend(['--username', username, '--password', password])

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to add repository: {result.stderr}")

        # Update repository cache
        self.update_repositories()

        return {
            'success': True,
            'message': result.stdout,
            'name': name,
            'url': url
        }

    def update_repositories(self) -> dict[str, Any]:
        """
        Update all Helm chart repositories.

        Returns:
            Result dictionary
        """
        result = subprocess.run(
            ['helm', 'repo', 'update'],
            capture_output=True,
            text=True,
            timeout=120
        )

        return {
            'success': result.returncode == 0,
            'exit_code': result.returncode,
            'output': result.stdout,
            'error': result.stderr
        }

    def remove_repository(self, name: str) -> dict[str, Any]:
        """
        Remove a Helm chart repository.

        Args:
            name: Repository name to remove

        Returns:
            Result dictionary
        """
        self._validate_arg("name", name)

        result = subprocess.run(
            ['helm', 'repo', 'remove', name],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            raise Exception(f"Failed to remove repository: {result.stderr}")

        return {"success": True, "message": f"Repository '{name}' removed successfully"}

    def list_repositories(self) -> list[dict[str, Any]]:
        """
        List all configured Helm chart repositories.

        Returns:
            List of repository dictionaries
        """
        result = subprocess.run(
            ['helm', 'repo', 'list', '--output', 'json'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return []

        try:
            return json.loads(result.stdout) if result.stdout.strip() else []
        except json.JSONDecodeError:
            return []

    def browse_charts(
        self,
        repository: str | None = None,
        max_results: int = 50
    ) -> list[dict[str, Any]]:
        """
        Browse all available charts (or from a specific repository).

        Args:
            repository: Specific repository name (e.g., 'bitnami', 'stable')
            max_results: Maximum number of results

        Returns:
            List of chart dictionaries
        """
        self._validate_arg("repository", repository)

        # Use empty string to match all charts, with specific repo if provided
        search_term = f"{repository}/" if repository else ""

        command = ['helm', 'search', 'repo', search_term or '.', '--output', 'json', '--max-col-width', '0']

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            logger.warning(f"helm search repo failed: {result.stderr}")
            return []

        try:
            charts = json.loads(result.stdout) if result.stdout.strip() else []
            # Limit results for browse to avoid overwhelming the UI
            return charts[:max_results]
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse helm search output: {e}")
            return []

    def search_charts(
        self,
        keyword: str,
        repository: str | None = None,
        max_results: int = 100
    ) -> list[dict[str, Any]]:
        """
        Search for charts in repositories.

        Args:
            keyword: Search keyword
            repository: Specific repository to search (optional)
            max_results: Maximum number of results

        Returns:
            List of chart dictionaries
        """
        self._validate_arg("keyword", keyword)
        self._validate_arg("repository", repository)

        command = ['helm', 'search', 'repo', keyword, '--output', 'json', '--max-col-width', '0']

        if repository:
            command[3] = f"{repository}/{keyword}"

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120  # Increased from 30s to 120s for large repository indexes
        )

        if result.returncode != 0:
            return []

        try:
            charts = json.loads(result.stdout) if result.stdout.strip() else []
            return charts[:max_results]
        except json.JSONDecodeError:
            return []
