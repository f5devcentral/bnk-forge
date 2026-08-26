from sqlalchemy import Column, Integer, String
from backend.db import Base

class CloudAccount(Base):
    __tablename__ = 'cloud_accounts'
    
    id = Column(Integer, primary_key=True)
    provider = Column(String)
    account_id = Column(String)
    credentials = Column(String)
