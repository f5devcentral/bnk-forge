from typing import List
from backend.adapters.base import DiscoveryAdapter, DiscoveredClusterData

class IBMDiscoveryAdapter(DiscoveryAdapter):
    def discover(self, credentials: dict) -> List[DiscoveredClusterData]:
        return [
            DiscoveredClusterData(
                provider="IBM Cloud",
                region="us-south",
                account_id="ibm-account-001",
                name="ibm-dev-iks"
            ),
            DiscoveredClusterData(
                provider="IBM Cloud",
                region="eu-de",
                account_id="ibm-account-001",
                name="ibm-prod-iks"
            )
        ]
