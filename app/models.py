from sqlalchemy import Column, Integer, String, Text

from .database import Base


class SystemSettings(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    prune_days_age = Column(Integer, default=7)
    delete_days_age = Column(Integer, default=30)
    gitlab_host = Column(String, default="https://gitlab.com")
    disk_min_free_gb = Column(Integer, default=5)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(Text, nullable=False)
    repo_url = Column(Text, nullable=False, default="")
    ref = Column(Text, nullable=False, default="")
    machine = Column(Text, nullable=False, default="")
    target = Column(Text, nullable=False, default="")
    status = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False)
    started_at = Column(Text, nullable=True)
    finished_at = Column(Text, nullable=True)
    exit_code = Column(Integer, nullable=True)
    recipe_id = Column(Text, default="")
    raw_recipe_yaml = Column(Text, default="")
    note = Column(Text, default="")
    created_by = Column(Text, default="")
    pinned = Column(Integer, default=0)
    cc_emails = Column(Text, default="")
