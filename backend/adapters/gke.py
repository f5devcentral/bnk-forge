from typing import List
from backend.adapters.base import DiscoveryAdapter, DiscoveredClusterData

class GKEDiscoveryAdapter(DiscoveryAdapter):
    def discover(self, credentials: dict) -> List[DiscoveredClusterData]:
        return [
            DiscoveredClusterData(
                provider="GKE",
                region="us-central1",
                account_id="my-gcp-project",
                name="gke-dev-cluster"
            ),
            DiscoveredClusterData(
                provider="GKE",
                region="europe-west1",
                account_id="my-gcp-project",
                name="gke-prod-cluster"
            )
        ]
