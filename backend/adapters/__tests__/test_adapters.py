import unittest
import unittest.mock
from backend.adapters.aws import AWSDiscoveryAdapter
from backend.adapters.azure import AzureDiscoveryAdapter
from backend.adapters.gke import GKEDiscoveryAdapter
from backend.adapters.ibm import IBMDiscoveryAdapter
from backend.adapters.base import DiscoveredClusterData

class TestDiscoveryAdapters(unittest.TestCase):
    def test_aws_adapter(self):
        adapter = AWSDiscoveryAdapter()
        clusters = adapter.discover({})
        self.assertIsInstance(clusters, list)
        self.assertGreater(len(clusters), 0)
        self.assertIsInstance(clusters[0], DiscoveredClusterData)
        self.assertEqual(clusters[0].provider, "AWS")

    def test_azure_adapter(self):
        adapter = AzureDiscoveryAdapter()
        clusters = adapter.discover({})
        self.assertIsInstance(clusters, list)
        self.assertGreater(len(clusters), 0)
        self.assertIsInstance(clusters[0], DiscoveredClusterData)
        self.assertEqual(clusters[0].provider, "Azure")

    def test_gke_adapter(self):
        adapter = GKEDiscoveryAdapter()
        clusters = adapter.discover({})
        self.assertIsInstance(clusters, list)
        self.assertGreater(len(clusters), 0)
        self.assertIsInstance(clusters[0], DiscoveredClusterData)
        self.assertEqual(clusters[0].provider, "GKE")

    def test_ibm_adapter(self):
        adapter = IBMDiscoveryAdapter()
        clusters = adapter.discover({})
        self.assertIsInstance(clusters, list)
        self.assertGreater(len(clusters), 0)
        self.assertIsInstance(clusters[0], DiscoveredClusterData)
        self.assertEqual(clusters[0].provider, "IBM Cloud")

    @unittest.mock.patch("backend.adapters.cluster.config")
    @unittest.mock.patch("backend.adapters.cluster.client")
    def test_cluster_adapter(self, mock_client, mock_config):
        # Setup mocks
        mock_core_v1 = unittest.mock.MagicMock()
        mock_client.CoreV1Api.return_value = mock_core_v1
        
        # Mock node
        mock_node = unittest.mock.MagicMock()
        mock_node.metadata.name = "test-node"
        mock_node.metadata.labels = {"node.kubernetes.io/instance-type": "t3.medium"}
        mock_core_v1.list_node.return_value.items = [mock_node]
        
        # Mock namespace
        mock_ns = unittest.mock.MagicMock()
        mock_ns.metadata.name = "default"
        mock_core_v1.list_namespace.return_value.items = [mock_ns]
        
        # Mock pod
        mock_pod = unittest.mock.MagicMock()
        mock_pod.metadata.name = "test-pod"
        mock_pod.status.phase = "Running"
        mock_core_v1.list_namespaced_pod.return_value.items = [mock_pod]
        
        # Mock service
        mock_svc = unittest.mock.MagicMock()
        mock_svc.metadata.name = "test-svc"
        mock_svc.spec.type = "ClusterIP"
        mock_svc.spec.cluster_ip = "10.0.0.1"
        mock_core_v1.list_namespaced_service.return_value.items = [mock_svc]

        from backend.adapters.cluster import ClusterLevelDiscoveryAdapter
        adapter = ClusterLevelDiscoveryAdapter()
        
        credentials = {
            "kubeconfig_path": "/fake/path",
            "provider": "TestProvider",
            "cluster_name": "TestCluster"
        }
        
        clusters = adapter.discover(credentials)
        self.assertIsInstance(clusters, list)
        self.assertEqual(len(clusters), 1)
        cluster = clusters[0]
        
        self.assertEqual(cluster.provider, "TestProvider")
        self.assertEqual(cluster.name, "TestCluster")
        self.assertEqual(cluster.namespaces, ["default"])
        self.assertEqual(cluster.nodes[0]["name"], "test-node")
        self.assertEqual(cluster.nodes[0]["instance_type"], "t3.medium")
        self.assertEqual(cluster.pods[0]["name"], "test-pod")
        self.assertEqual(cluster.services[0]["name"], "test-svc")
        
        # Verify kubeconfig was loaded
        mock_config.load_kubeconfig.assert_called_with(config_file="/fake/path", context=None)

if __name__ == "__main__":
    unittest.main()
