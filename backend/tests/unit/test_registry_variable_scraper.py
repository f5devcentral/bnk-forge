"""Unit tests for the registry variable scraper.

Validates that python-hcl2 parsing of variables.tf / outputs.tf produces the
expected ScrapeResult structure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.registry_variable_scraper import scrape


@pytest.fixture
def module_dir(tmp_path: Path) -> Path:
    return tmp_path / "module"


def write_tf(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scrape_simple_variable(module_dir: Path) -> None:
    write_tf(module_dir / "variables.tf", '''
variable "region" {
  type        = string
  description = "AWS region"
  default     = "us-east-1"
}
''')

    result = scrape(module_dir)

    assert len(result.variables) == 1
    var = result.variables[0]
    assert var.name == "region"
    assert var.description == "AWS region"
    assert var.has_default is True
    assert var.default == "us-east-1"
    assert var.sensitive is False


def test_scrape_required_variable_no_default(module_dir: Path) -> None:
    write_tf(module_dir / "variables.tf", '''
variable "vpc_id" {
  type        = string
  description = "Existing VPC ID"
}
''')

    result = scrape(module_dir)

    assert len(result.variables) == 1
    assert result.variables[0].has_default is False
    assert result.variables[0].default is None


def test_scrape_sensitive_variable(module_dir: Path) -> None:
    write_tf(module_dir / "variables.tf", '''
variable "api_token" {
  type      = string
  sensitive = true
}
''')

    result = scrape(module_dir)
    assert result.variables[0].sensitive is True


def test_scrape_outputs(module_dir: Path) -> None:
    write_tf(module_dir / "outputs.tf", '''
output "vpc_id" {
  value       = aws_vpc.main.id
  description = "ID of the VPC"
}

output "secret_arn" {
  value     = aws_secret.main.arn
  sensitive = true
}
''')

    result = scrape(module_dir)
    assert len(result.outputs) == 2
    by_name = {o.name: o for o in result.outputs}
    assert by_name["vpc_id"].description == "ID of the VPC"
    assert by_name["secret_arn"].sensitive is True


def test_scrape_required_providers(module_dir: Path) -> None:
    write_tf(module_dir / "versions.tf", '''
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    random = {
      source = "hashicorp/random"
    }
  }
}
''')

    result = scrape(module_dir)
    assert "aws" in result.required_providers
    assert "random" in result.required_providers


def test_scrape_skips_subdirectories(module_dir: Path) -> None:
    write_tf(module_dir / "variables.tf", '''
variable "root_var" { type = string }
''')
    write_tf(module_dir / "modules" / "submod" / "variables.tf", '''
variable "sub_var" { type = string }
''')

    result = scrape(module_dir)
    names = [v.name for v in result.variables]
    assert "root_var" in names
    assert "sub_var" not in names


def test_scrape_missing_directory_returns_warning(tmp_path: Path) -> None:
    result = scrape(tmp_path / "does-not-exist")
    assert result.variables == []
    assert any("does not exist" in w for w in result.parse_warnings)


def test_scrape_no_tf_files_returns_warning(module_dir: Path) -> None:
    module_dir.mkdir()
    (module_dir / "README.md").write_text("readme only")

    result = scrape(module_dir)
    assert result.variables == []
    assert any("no *.tf files" in w for w in result.parse_warnings)
