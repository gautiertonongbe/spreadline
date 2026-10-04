"""Create an account, or set a password on one that has none.

The way the first account gets a password. There is no self-registration in the
API and no default credential in the database: a product that manages capital
should not ship with a password that anybody who has read the repository knows.

The password is read from a prompt rather than an argument, because an argument
is in the shell history and in the process list.

    python -m scripts.create_user --email you@example.com
    python -m scripts.create_user --email you@example.com --reset
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.database import session_scope
from app.core.errors import SpreadlineError
from app.domains.tenancy import auth as auth_service
from app.domains.tenancy.service import ensure_default_organization
from app.models.tenancy import User


def _read_password() -> str:
    first = getpass.getpass("Password: ")
    second = getpass.getpass("Repeat: ")
    if first != second:
        print("The two passwords do not match.", file=sys.stderr)
        raise SystemExit(2)
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=None, help="Prompted for if omitted.")
    parser.add_argument("--name", default=None)
    parser.add_argument(
        "--role",
        default="owner",
        help="owner may add other accounts; operator may not.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Set the password on an existing account, ending its other sessions.",
    )
    args = parser.parse_args()

    address = (args.email or input("Email: ")).strip().lower()
    with session_scope() as session:
        organization = ensure_default_organization(session)
        existing = session.scalar(
            select(User).where(
                User.organization_id == organization.id, User.email == address
            )
        )

        try:
            if existing is not None:
                if not args.reset and existing.password_hash is not None:
                    print(
                        f"{address} already has an account with a password. "
                        "Pass --reset to set a new one.",
                        file=sys.stderr,
                    )
                    return 1
                password = _read_password()
                ended = auth_service.reset_password(
                    session,
                    existing,
                    new=password,
                    reason="password reset from the command line",
                )
                print(f"Password set for {address}. {ended} session(s) ended.")
                return 0

            password = _read_password()
            user = auth_service.create_user(
                session,
                organization,
                email=address,
                password=password,
                full_name=args.name,
                role=args.role,
            )
            print(f"Created {user.email} as {user.role} in {organization.name}.")
            return 0
        except SpreadlineError as error:
            print(error.detail, file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
