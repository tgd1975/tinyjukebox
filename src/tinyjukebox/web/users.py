"""tinyjukebox-users - manage the users of the rest api.

    tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml add mama --role admin
    tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml add kind            # role 'user': player, volume, buttons
    tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml passwd mama
    tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml role kind admin
    tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml remove kind
    tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml list

The running service picks up changes immediately. Admins can do the same on the web page (tab "System"); every
user can change the own password there.
"""

import argparse
import getpass
import sys
from collections.abc import Sequence

from tinyjukebox.web.auth import (
    MAX_USERNAME_LENGTH,
    MIN_PASSWORD_LENGTH,
    ROLES,
    UsersFile,
    hash_password,
    valid_username,
)

__all__ = ["MIN_PASSWORD_LENGTH", "main"]


def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("password: ")
        if getpass.getpass("repeat password: ") != password:
            raise SystemExit("passwords do not match.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"password must have at least {MIN_PASSWORD_LENGTH} characters.")
    return password


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tinyjukebox-users", description="manage the users of the Tiny Jukebox rest api."
    )
    parser.add_argument("-f", "--file", required=True, help="users file (rest-api/users-file in the config)")
    parser.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="add a user")
    add.add_argument("name")
    add.add_argument("--role", choices=ROLES, default="user")
    commands.add_parser("passwd", help="change the password of a user").add_argument("name")
    role = commands.add_parser("role", help="change the role of a user")
    role.add_argument("name")
    role.add_argument("role", choices=ROLES)
    commands.add_parser("remove", help="remove a user").add_argument("name")
    commands.add_parser("list", help="list all users")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        users = UsersFile.read(args.file)
    except ValueError as e:
        raise SystemExit(str(e)) from None

    if args.command == "list":
        for name, user in sorted(users.items()):
            print(f"{name} ({user['role']})")
        return
    if args.command == "add":
        if args.name in users:
            raise SystemExit(f"user '{args.name}' exists already.")
        if not valid_username(args.name):
            raise SystemExit(
                f"invalid user name: no blanks around it, no ':', at most {MAX_USERNAME_LENGTH} characters."
            )
        users[args.name] = {"password-hash": hash_password(_read_password(args)), "role": args.role}
    else:
        if args.name not in users:
            raise SystemExit(f"unknown user '{args.name}'.")
        if args.command == "passwd":
            users[args.name]["password-hash"] = hash_password(_read_password(args))
        elif args.command == "role":
            users[args.name]["role"] = args.role
        elif args.command == "remove":
            del users[args.name]
    UsersFile.write(args.file, users)
    print(f"{args.command}: {args.name} - done.")


if __name__ == "__main__":
    main()
