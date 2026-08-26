from typing import List
from backend.adapters.base import DiscoveryAdapter, DiscoveredClusterData

class AzureDiscoveryAdapter(DiscoveryAdapter):
    def discover(self, credentials: dict) -> List[DiscoveredClusterData]:
        return [
            DiscoveredClusterData(
                provider="Azure",
                region="eastus",
                account_id="azure-sub-123",
                name="azure-dev-aks"
            ),
            DiscoveredClusterData(
                provider="Azure",
                region="westus2",
                account_id="azure-sub-123",
                name="azure-prod-aks"
            )
        ]
