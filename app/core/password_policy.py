"""Rules for a new Hyperlite account password.

Checked on the server whenever a password is set: an administrator creating an account or resetting its
password, or users changing their own. The dashboard mirrors the main rules for instant feedback; the server
stays the authority. Passwords already in place are not affected.

Length is what makes a password hard to guess (NIST SP 800-63B): at least 12 characters and no composition
rules. On top of that, the passwords an attacker tries first are refused: the account name, a run of a few
characters, keyboard and alphabet sequences, and well-known passwords even with digits, symbols or look-alike
letters around them. bcrypt ignores everything past 72 bytes, so a longer password is refused rather than
silently cut (app/core/passwords.py).
"""

MIN_LENGTH = 12
MAX_BYTES = 72
MIN_DISTINCT = 5

_SEQUENCES = (
    "0123456789",
    "abcdefghijklmnopqrstuvwxyz",
    "azertyuiopqsdfghjklmwxcvbn",  # French keyboard rows
    "qwertyuiopasdfghjklzxcvbnm",
    "qwertzuiopasdfghjklyxcvbnm",  # German keyboard rows
)

# Base words of the passwords tried first by attackers (lower case, look-alike letters already undone).
_COMMON = frozenset(
    {
        "password",
        "motdepasse",
        "azerty",
        "azertyuiop",
        "qwerty",
        "qwertyuiop",
        "admin",
        "administrator",
        "administrateur",
        "hyperlite",
        "hypervisor",
        "hyperviseur",
        "root",
        "toor",
        "welcome",
        "bienvenue",
        "letmein",
        "changeme",
        "secret",
        "default",
        "iloveyou",
        "jetaime",
        "soleil",
        "doudou",
        "loulou",
        "marseille",
        "proxmox",
        "vmware",
        "libvirt",
        "ubuntu",
        "debian",
        "monkey",
        "dragon",
        "football",
        "sunshine",
        "princess",
        "master",
        "superman",
        "trustno",
        "login",
        "user",
        "guest",
    }
)

_LOOKALIKE = str.maketrans({"@": "a", "4": "a", "0": "o", "1": "i", "!": "i", "3": "e", "$": "s", "5": "s", "7": "t"})


def _is_sequence(low: str) -> bool:
    for seq in _SEQUENCES:
        wrapped = seq * 3  # also catches runs that wrap around, e.g. "7890123456789"
        if low in wrapped or low in wrapped[::-1]:
            return True
    return False


def _base_word(low: str) -> str:
    """The word left once the digits and symbols around it are removed and look-alike letters undone:
    "P@ssw0rd2024!" -> "password", "Azerty123!" -> "azerty"."""
    # A plain scan rather than a regular expression: a trailing-run pattern such as [\W\d_]+$ backtracks
    # quadratically on long runs of digits (CodeQL py/polynomial-redos).
    start, end = 0, len(low)
    while start < end and not low[start].isalpha():
        start += 1
    while end > start and not low[end - 1].isalpha():
        end -= 1
    return low[start:end].translate(_LOOKALIKE)


def password_problem(password: str, username: str | None = None) -> str | None:
    """Return why this password is refused (the sentence sent back to the client), or None if it is accepted."""
    if len(password) < MIN_LENGTH:
        return f"The password must contain at least {MIN_LENGTH} characters"
    if len(password.encode("utf-8")) > MAX_BYTES:
        return f"The password must not exceed {MAX_BYTES} bytes (about {MAX_BYTES} characters without accents)"
    low = password.lower()
    if username and len(username) >= 3 and username.lower() in low:
        return "The password must not contain the account name"
    if len(set(low)) < MIN_DISTINCT:
        return "The password is too repetitive: use more different characters"
    if _is_sequence(low):
        return "The password must not be a keyboard or alphabet sequence"
    if low in _COMMON or _base_word(low) in _COMMON:
        return "This password is too common: it is among the first ones attackers try"
    return None
