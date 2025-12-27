from sqlalchemy import Column, Integer, String

from .database import Base


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    prune_days_age = Column(Integer, default=7)
    delete_days_age = Column(Integer, default=30)
    gitlab_host = Column(String, default="https://gitlab.com")
    disk_min_free_gb = Column(Integer, default=5)
