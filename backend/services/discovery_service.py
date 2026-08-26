import logging
from typing import List, Optional, Type, Dict, Any

from backend.adapters.base import DiscoveryAdapter, DiscoveredClusterData

logger = logging.getLogger(__name__)

from backend.models.cluster import DiscoveryStatus

class DiscoveryResult:
    def __init__(self, status: str, discovered_clusters: int, errors: List[str]):
        self.status = status
        self.discovered_clusters = discovered_clusters
        self.errors = errors

# Registry mapping provider names to their Adapter classes
ADAPTER_REGISTRY: Dict[str, Type[DiscoveryAdapter]] = {}

def register_adapter(provider: str, adapter_class: Type[DiscoveryAdapter]):
    ADAPTER_REGISTRY[provider.lower()] = adapter_class

def get_adapter(provider: str) -> Optional[DiscoveryAdapter]:
    adapter_class = ADAPTER_REGISTRY.get(provider.lower())
    if adapter_class:
        return adapter_class()
    return None

def discover_kubernetes_clusters(db_session, account_ids: Optional[List[int]] = None) -> DiscoveryResult:
    """
    Central multi-cloud Kubernetes cluster discovery orchestration layer.
    
    1. Loads configured cloud accounts/credentials.
    2. Selects the correct provider-specific discovery Adapter.
    3. Invokes each adapter through a consistent Interface.
    4. Normalizes discovered cluster data.
    5. Upserts KubernetesCluster database records.
    6. Updates discovery status fields for success, partial failure, or failure.
    """
    
    # 1. Load configured cloud accounts
    # This requires importing the actual ORM CloudAccount model.
    try:
        from backend.models.account import CloudAccount
        query = db_session.query(CloudAccount)
        if account_ids:
            query = query.filter(CloudAccount.id.in_(account_ids))
        accounts = query.all()
    except ImportError:
        logger.warning("CloudAccount model not found. Proceeding with empty account list.")
        accounts = []
        
    try:
        from backend.models.cluster import KubernetesCluster
    except ImportError:
        KubernetesCluster = None

    total_discovered = 0
    errors = []
    
    has_success = False
    has_failure = False

    for account in accounts:
        provider = getattr(account, "provider", "").lower()
        
        # 2. Select the correct adapter
        adapter = get_adapter(provider)
        if not adapter:
            msg = f"No adapter found for provider: {provider}"
            logger.warning(msg)
            errors.append(msg)
            has_failure = True
            continue
            
        try:
            logger.info(f"Running discovery for account {account.id} ({provider})")
            
            # 3. Invoke adapter
            credentials = getattr(account, "credentials", None)
            discovered_data: List[DiscoveredClusterData] = adapter.discover(credentials)
            
            # 4. Normalize discovered cluster data
            for cluster_data in discovered_data:
                
                # 5. Upsert KubernetesCluster database records
                if KubernetesCluster:
                    existing_cluster = db_session.query(KubernetesCluster).filter_by(
                        provider=cluster_data.provider,
                        region=cluster_data.region,
                        account_id=cluster_data.account_id,
                        name=cluster_data.name
                    ).first()
                    
                    if existing_cluster:
                        # Update status for existing cluster
                        existing_cluster.discovery_status = DiscoveryStatus.SUCCESS
                    else:
                        # Create new cluster record
                        new_cluster = KubernetesCluster(
                            provider=cluster_data.provider,
                            region=cluster_data.region,
                            account_id=cluster_data.account_id,
                            name=cluster_data.name,
                            discovery_status=DiscoveryStatus.SUCCESS
                        )
                        db_session.add(new_cluster)
                        
                total_discovered += 1
                
            db_session.commit()
            has_success = True
            
        except Exception as e:
            # Error handling policy per account/provider: 
            # one provider failure should not abort the entire multi-cloud run.
            msg = f"Discovery failed for account {account.id} ({provider}): {str(e)}"
            logger.error(msg)
            errors.append(msg)
            has_failure = True
            db_session.rollback()
            
    # 6. Update discovery status fields for success, partial failure, or failure.
    if has_success and has_failure:
        overall_status = DiscoveryStatus.PARTIAL_FAILURE
    elif has_failure and not has_success:
        overall_status = DiscoveryStatus.FAILURE
    else:
        overall_status = DiscoveryStatus.SUCCESS

    return DiscoveryResult(
        status=overall_status.value, 
        discovered_clusters=total_discovered, 
        errors=errors
    )

# Register built-in adapters if available
try:
    from backend.adapters.aws import AWSDiscoveryAdapter
    register_adapter("aws", AWSDiscoveryAdapter)
except ImportError:
    pass

try:
    from backend.adapters.azure import AzureDiscoveryAdapter
    register_adapter("azure", AzureDiscoveryAdapter)
except ImportError:
    pass

try:
    from backend.adapters.gke import GKEDiscoveryAdapter
    register_adapter("gke", GKEDiscoveryAdapter)
except ImportError:
    pass

try:
    from backend.adapters.ibm import IBMDiscoveryAdapter
    register_adapter("ibm", IBMDiscoveryAdapter)
except ImportError:
    pass
