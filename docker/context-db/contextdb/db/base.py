from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.automap import automap_base


DbBase = declarative_base()
AutoDbBase = automap_base(declarative_base=DbBase)


class ContextDbMixin:
	__table_args__ = { 'extend_existing': True }