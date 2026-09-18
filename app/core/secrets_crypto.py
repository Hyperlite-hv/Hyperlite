"""Chiffrement au repos des secrets stockes en base (2026-09-18,
backlog du chantier 28/20) -- le mot de passe SMTP (chantier 28) et le
client secret OIDC (chantier 20) etaient stockes en clair dans
`hyperlite.db`, documente a l'epoque comme acceptable ("pas de
coffre-fort de secrets dans ce projet").

**Limite honnete, a ne pas se cacher** : la cle de chiffrement vit dans
le MEME fichier `.env` que le reste de la config, sur le MEME disque que
`hyperlite.db` -- un attaquant avec un acces COMPLET au systeme de
fichiers n'est PAS arrete par ce chiffrement (ce projet n'a pas de
coffre-fort externe/KMS, et n'en aura probablement jamais vu l'echelle).
Protection reelle et non nulle contre un scenario plus etroit mais
realiste : une fuite du SEUL fichier `hyperlite.db` (sauvegarde copiee/
partagee sans le `.env` qui l'accompagne, extraction SQL limitee,
dump partiel...).

Fernet (module `cryptography`, deja present -- dependance transitive de
python-jose[cryptography] deja utilise par security.py/sso.py) : AES-128
en mode CBC + HMAC-SHA256, chiffrement authentifie, format eprouve et
simple plutot que de reinventer un schema.
"""
import os
from pathlib import Path

from cryptography.fernet import Fernet

ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
_KEY_VAR = "HYPERLITE_ENCRYPTION_KEY"
_fernet = None


def _read_key_from_env_file():
    """Le service tourne sous systemd (EnvironmentFile=.env, voir
    hyperlite.service) -- os.environ la contient toujours pour LUI. Mais
    un script/outil lance a la main (hors systemd, ex. un test) ne
    l'herite PAS automatiquement meme si .env contient deja la variable
    -- BUG REEL rencontre en testant ce chantier : un tel script a
    regenere une DEUXIEME cle et l'a re-appendue a .env (repli sur
    os.environ seul, jamais verifie le fichier lui-meme), rendant les
    secrets deja chiffres avec la premiere cle illisibles pour ce
    process. Corrige en lisant le fichier directement en dernier
    recours, AVANT de conclure qu'aucune cle n'existe encore."""
    if not ENV_PATH.exists():
        return None
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{_KEY_VAR}="):
            return line.split("=", 1)[1].strip()
    return None


def _load_or_create_key():
    key = os.environ.get(_KEY_VAR) or _read_key_from_env_file()
    if key:
        os.environ[_KEY_VAR] = key
        return key.encode()
    # Vraiment aucune cle nulle part (premiere fois que CETTE machine
    # rencontre ce besoin) -- en genere une et la PERSISTE immediatement
    # dans .env : une cle perdue au prochain redemarrage rendrait tout
    # secret deja chiffre illisible pour toujours (aucune autre copie
    # nulle part).
    new_key = Fernet.generate_key()
    with open(ENV_PATH, "a") as f:
        f.write(f"\n{_KEY_VAR}={new_key.decode()}\n")
    os.chmod(ENV_PATH, 0o600)
    os.environ[_KEY_VAR] = new_key.decode()
    return new_key


def _get_fernet():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext):
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(value):
    """Repli SILENCIEUX sur la valeur telle quelle si le dechiffrement
    echoue -- couvre la migration depuis une valeur DEJA EN BASE avant ce
    chantier (jamais chiffree, donc pas un jeton Fernet valide) : plutot
    que de faire planter un envoi SMTP/une connexion OIDC existante,
    l'ancienne valeur en clair continue de fonctionner jusqu'a la
    prochaine fois qu'un admin la modifie (qui la chiffrera alors)."""
    if not value:
        return value
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except Exception:
        return value
