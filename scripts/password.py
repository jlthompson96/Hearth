"""Set the owner's password: `make password`.

Asks twice, without echoing, and prints the line for `.env` — the scrypt hash,
never the password. Hearth reads only the hash (api/auth.py). Changing it signs
every device out, because sessions are signed with a key derived from it.
"""

import getpass
import sys

from api.auth import hash_password

#: Short enough to type on a phone; long enough that the throttle's five
#: guesses a quarter of an hour are no way in.
MINIMUM = 12


def main() -> None:
    password = getpass.getpass("New Hearth password: ")
    if len(password) < MINIMUM:
        sys.exit(f"At least {MINIMUM} characters.")
    if getpass.getpass("Again: ") != password:
        sys.exit("The two did not match.")
    print("\nPut this line in .env (the NAS project's, when Hearth runs there):\n")
    print(f"HEARTH_PASSWORD_HASH={hash_password(password)}")


if __name__ == "__main__":
    main()
