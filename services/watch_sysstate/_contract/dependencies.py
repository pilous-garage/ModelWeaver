"""Dépendances du service `watch_sysstate`."""
from modules.checker.checker import Checker

CONSUMES = {
    "modules.checker.checker": ["Checker"],
    "services._common": ["acquire_instance_lock"],
}
