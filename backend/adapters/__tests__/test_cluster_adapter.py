import unittest
from backend.adapters.cluster import ClusterLevelDiscoveryAdapter
from backend.adapters.base import DiscoveredClusterData

class TestClusterAdapter(unittest.TestCase):
    def test_cluster_adapter(self):
        adapter = ClusterLevelDiscoveryAdapter()
        clusters = adapter.discover({})
        self.assertIsInstance(clusters, list)
        self.assertGreater(len(clusters), 0)
        self.assertIsInstance(clusters[0], DiscoveredClusterData)
        self.assertEqual(clusters[0].provider, "ClusterAPI")

if __name__ == "__main__":
    unittest.main()
