"""Unit tests for _inject_cluster_kubeconfig in variable_assembler."""

from unittest.mock import MagicMock, patch

import pytest

from services.execution.variable_assembler import _inject_cluster_kubeconfig
from services.kubeconfig_normalizer import NormalizationSource


class TestInjectClusterKubeconfig:
    """Tests for _inject_cluster_kubeconfig helper."""

    def _make_lib_module(self, schema_names: list[str]) -> MagicMock:
        lib_module = MagicMock()
        lib_module.variables_schema = [{"name": n} for n in schema_names]
        return lib_module

    def _make_cluster(self, kubeconfig_encrypted: str | None) -> MagicMock:
        cluster = MagicMock()
        cluster.kubeconfig_encrypted = kubeconfig_encrypted
        cluster.name = "test-cluster"
        return cluster

    def test_kubeconfig_injected_when_module_declares_it(self):
        variables: dict = {}
        lib_module = self._make_lib_module(["kubeconfig_content", "other_var"])
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value", return_value="kc-yaml"), \
             patch(
                 "services.execution.variable_assembler.normalize_kubeconfig",
                 side_effect=lambda content, source: content,
             ) as mock_normalize:
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)

        assert variables["kubeconfig_content"] == "kc-yaml"
        mock_normalize.assert_called_once_with("kc-yaml", source=NormalizationSource.INTERNAL_REREAD)

    def test_kubeconfig_not_injected_when_module_does_not_declare_it(self):
        variables: dict = {}
        lib_module = self._make_lib_module(["some_other_var"])
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value", return_value="kc-yaml") as mock_dec:
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)
            mock_dec.assert_not_called()

        assert "kubeconfig_content" not in variables

    def test_kubeconfig_not_injected_when_cluster_has_no_encrypted_kubeconfig(self):
        variables: dict = {}
        lib_module = self._make_lib_module(["kubeconfig_content"])
        first_cluster = self._make_cluster(None)

        with patch("services.execution.variable_assembler.decrypt_value") as mock_dec:
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)
            mock_dec.assert_not_called()

        assert "kubeconfig_content" not in variables

    def test_kubeconfig_not_injected_when_lib_module_is_none(self):
        variables: dict = {}
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value") as mock_dec:
            _inject_cluster_kubeconfig(variables, None, first_cluster)
            mock_dec.assert_not_called()

        assert "kubeconfig_content" not in variables

    def test_kubeconfig_not_injected_when_decrypt_returns_empty(self):
        variables: dict = {}
        lib_module = self._make_lib_module(["kubeconfig_content"])
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value", return_value=""):
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)

        assert "kubeconfig_content" not in variables

    def test_kubeconfig_decrypt_exception_is_swallowed(self):
        variables: dict = {}
        lib_module = self._make_lib_module(["kubeconfig_content"])
        first_cluster = self._make_cluster("bad-enc-data")

        with patch("services.execution.variable_assembler.decrypt_value", side_effect=RuntimeError("bad key")):
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)  # must not raise

        assert "kubeconfig_content" not in variables

    def test_user_override_falsy_value_is_replaced_by_injected_kubeconfig(self):
        """Regression for review point 1: a blank/placeholder user override
        (e.g. the historical "<username>" sentinel resolved to an empty
        string upstream) must not block auto-injection."""
        variables: dict = {"kubeconfig_content": ""}
        lib_module = self._make_lib_module(["kubeconfig_content"])
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value", return_value="kc-yaml"), \
             patch(
                 "services.execution.variable_assembler.normalize_kubeconfig",
                 side_effect=lambda content, source: content,
             ):
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)

        assert variables["kubeconfig_content"] == "kc-yaml"

    def test_genuine_user_supplied_kubeconfig_is_not_overwritten(self):
        """Regression for review point 1: a real user-supplied kubeconfig
        value must win over the registered cluster's kubeconfig."""
        variables: dict = {"kubeconfig_content": "user-supplied-kc-yaml"}
        lib_module = self._make_lib_module(["kubeconfig_content"])
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value") as mock_dec:
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)
            mock_dec.assert_not_called()

        assert variables["kubeconfig_content"] == "user-supplied-kc-yaml"

    def test_variables_schema_as_dict_does_not_crash_and_does_not_inject(self):
        """Regression for review point 3: ModuleLibrary.variables_schema can be
        a dict keyed by variable name rather than a list of dicts. Iterating a
        dict yields string keys, so `isinstance(v, dict)` must safely evaluate
        to False instead of crashing on `str.get`."""
        variables: dict = {}
        lib_module = MagicMock()
        lib_module.variables_schema = {"kubeconfig_content": {"type": "string"}}
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value") as mock_dec:
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)
            mock_dec.assert_not_called()

        assert "kubeconfig_content" not in variables

    def test_normalization_failure_is_swallowed(self):
        """normalize_kubeconfig can raise KubeconfigUnportableError for a
        legacy non-portable stored kubeconfig; injection must degrade to a
        warning rather than propagate, matching the existing try/except."""
        variables: dict = {}
        lib_module = self._make_lib_module(["kubeconfig_content"])
        first_cluster = self._make_cluster("enc-kc-data")

        with patch("services.execution.variable_assembler.decrypt_value", return_value="kc-yaml"), \
             patch(
                 "services.execution.variable_assembler.normalize_kubeconfig",
                 side_effect=RuntimeError("unportable"),
             ):
            _inject_cluster_kubeconfig(variables, lib_module, first_cluster)  # must not raise

        assert "kubeconfig_content" not in variables
