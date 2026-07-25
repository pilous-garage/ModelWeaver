"""Interface publique du module `system` : dépendances système."""
from modules.system.deps import install_system_package, install_target_dependencies

__all__ = ['install_system_package', 'install_target_dependencies']
