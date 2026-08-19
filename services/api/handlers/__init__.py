from services.api.handlers import system
from services.api.handlers import keys
from services.api.handlers import llm
from services.api.handlers import jobs_handlers as jobsh
from services.api.handlers import usage
from services.api.handlers import agents
from services.api.handlers import services_handlers as srvh
from services.api.handlers import chat
from services.api.handlers import teams_handlers as teamsh
from services.api.handlers import llm_allocation as llm_alloc
from services.api.handlers import catalogue_bundles
from services.api.handlers import catalogue_tools
from services.api.handlers import layouts
from services.api.handlers import docker_handlers as dockerh
from services.api.handlers import workspace
from services.api.handlers import pause
from services.api.handlers import panels
from services.api.handlers import gui
from services.api.handlers import monitoring
from services.api.handlers import dev_chat
from services.api.handlers import auth
from services.api.handlers import windows_store
from services.api.handlers import graphe_utile
from services.api.handlers import utils
from services.api.handlers import openai_compat
from services.api.handlers import infra  # routes /infra/* sur domaines sqlite

# Helpers legacy (catalogue_local, catalogue_tools…) dépendent de modules.sql
# (legacy). Import optionnel : le daemon boote même si cassés, les routes
# concernées restent indisponibles (graceful degradation).
try:
    from services.api.handlers import catalogue_local
except Exception as _legacy_err:
    catalogue_local = _legacy_err
try:
    from services.api.handlers import catalogue_tools as _cat_tools
except Exception as _legacy_err:
    _cat_tools = _legacy_err
