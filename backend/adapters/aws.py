from typing import List
from backend.adapters.base import DiscoveryAdapter, DiscoveredClusterData

class AWSDiscoveryAdapter(DiscoveryAdapter):
    def discover(self, credentials: dict) -> List[DiscoveredClusterData]:
        return [
            DiscoveredClusterData(
                provider="AWS",
                region="us-east-1",
                account_id="123456789012",
                name="aws-dev-eks"
            ),
            DiscoveredClusterData(
                provider="AWS",
                region="us-west-2",
                account_id="123456789012",
                name="aws-prod-eks"
            )
        ]
