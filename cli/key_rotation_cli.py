import sys
import argparse
from modules.key_rotation.rotation_module import KeyRotationService
from services.key_rotation_service import KeyRotationREST

def main():
    parser = argparse.ArgumentParser(description="ModelWeaver API Key Rotation CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Rotate command
    rot_parser = subparsers.add_parser("rotate", help="Rotate a specific key or all keys under a strategy")
    rot_parser.add_argument("--key-ref", help="Specific key reference to rotate")
    rot_parser.add_argument("--strategy", help="Strategy name (e.g. AutomaticRotationStrategy or all)")

    # Check background check command
    subparsers.add_parser("check", help="Check and rotate expired keys")

    # Cleanup grace periods command
    subparsers.add_parser("cleanup-grace", help="Revoke keys whose grace period has expired")

    args = parser.parse_args()

    if args.command == "rotate":
        res = KeyRotationREST.manual_rotate(key_ref=args.key_ref, strategy=args.strategy)
        print(res)
    elif args.command == "check":
        service = KeyRotationService()
        res = service.check_and_rotate_expired()
        print(res)
    elif args.command == "cleanup-grace":
        res = KeyRotationREST.trigger_grace_cleanup()
        print(res)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
