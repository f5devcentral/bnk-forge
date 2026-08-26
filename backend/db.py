from sqlalchemy.orm import declarative_base

Base = declarative_base()

class Session:
    def query(self, *args, **kwargs):
        pass
    def add(self, *args, **kwargs):
        pass
    def commit(self):
        pass
    def rollback(self):
        pass
