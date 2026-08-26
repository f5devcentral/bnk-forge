from enum import Enum as PyEnum
from sqlalchemy import Column, Integer, String, Enum
from backend.db import Base

class DiscoveryStatus(PyEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILURE = "FAILURE"

class KubernetesCluster(Base):
    __tablename__ = 'kubernetes_clusters'
    
    id = Column(Integer, primary_key=True)
    provider = Column(String)
    region = Column(String)
    account_id = Column(String)
    name = Column(String)
    discovery_status = Column(Enum(DiscoveryStatus), default=DiscoveryStatus.PENDING)
