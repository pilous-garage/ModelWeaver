"""Point d'entrée pour lancer le Ticker en ligne de commande.

Usage:
    python -m agents               # Démarre le ticker (surveille AgentManager)
    python -m agents --list-roles  # Liste les rôles disponibles
"""

import argparse
import asyncio
import logging
import sys

from AgentFrameWork.ticker import AsyncTicker
from AgentsCatalogue.role_manager import RoleManager


def main():
    parser = argparse.ArgumentParser(description="Agent OS — Ticker")
    parser.add_argument("--list-roles", action="store_true", help="Liste les rôles disponibles")
    parser.add_argument("--poll", type=float, default=5.0, help="Intervalle de polling (secondes)")
    parser.add_argument("--debug", action="store_true", help="Logs debug")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.list_roles:
        rm = RoleManager()
        roles = rm.list_roles()
        if roles:
            print("Rôles disponibles :")
            for name in roles:
                role = rm.get_role(name)
                print(f"  • {name}: {role.description}")
        else:
            print("Aucun rôle trouvé dans AgentsCatalogue/rôles/")
        return

    ticker = AsyncTicker(poll_interval=args.poll)

    try:
        asyncio.run(ticker.start())
    except KeyboardInterrupt:
        print("\nArrêt demandé...")
        asyncio.run(ticker.stop())


if __name__ == "__main__":
    main()
