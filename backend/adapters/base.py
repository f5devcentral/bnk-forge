from typing import List, Optional, Dict, Any
from abc import ABC, abstractmethod
from pydantic import BaseModel

class DiscoveredClusterData(BaseModel):
    provider: str
    region: str
    account_id: str
    name: str
    namespaces: Optional[List[str]] = None
    pods: Optional[List[Dict[str, Any]]] = None
    services: Optional[List[Dict[str, Any]]] = None
    nodes: Optional[List[Dict[str, Any]]] = None

class DiscoveryAdapter(ABC):
    @abstractmethod
    def discover(self, credentials: dict) -> List[DiscoveredClusterData]:
        pass
