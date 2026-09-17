"""Pare-feu reseau/datacenter (chantier 21, 2026-09-17). Distinct du
pare-feu PAR VM (chantier 9, app/routers/vms.py, sous-systeme nwfilter de
libvirt) : celui-ci filtre au niveau du PONT lui-meme (chaine FORWARD du
noyau), donc s'applique a TOUTES les VM d'un reseau, presentes et futures,
sans devoir toucher chaque interface individuellement.

Pourquoi pas nwfilter ici : verifie contre les schemas RNG de libvirt sur
cet hote (/usr/share/libvirt/schemas/network.rng et nwfilter.rng) --
`<filterref>` n'existe QUE dans le schema du domaine (interface de VM), le
schema `<network>` n'a aucune notion de "filtre par defaut applique a
toutes les interfaces de ce reseau". Un pare-feu reellement au niveau
reseau doit donc filtrer le point de passage reel du trafic : le pont
Linux associe au reseau, via iptables/FORWARD -- exactement la ou libvirt
lui-meme insere deja ses propres chaines pour le NAT (LIBVIRT_FWI/FWO/FWX,
verifiees presentes sur cet hote pour chaque reseau nat/isole demarre).

Notre chaine (HYPERLITENETFW) est inseree en position 1 de FORWARD, donc
evaluee AVANT les chaines de libvirt -- un DROP explicite ici bloque le
trafic avant meme que libvirt n'ait la moindre chance de l'autoriser."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

import libvirt

from app.core.database import get_conn

UMBRELLA_CHAIN = "HYPERLITENETFW"


def _chain_name(network_name: str) -> str:
    # Limite reelle d'un nom de chaine iptables : 28 caracteres. Un hash
    # deterministe plutot qu'une troncature du nom : deux reseaux dont le
    # nom partagerait les 20 premiers caracteres ne doivent jamais finir
    # sur la meme chaine.
    h = hashlib.sha1(network_name.encode("utf-8")).hexdigest()[:16].upper()
    return f"HLNET{h}"


def _run(*args):
    return subprocess.run(["iptables", *args], capture_output=True, text=True)


def _chain_exists(chain: str) -> bool:
    return _run("-nL", chain).returncode == 0


def _ensure_umbrella_chain():
    if not _chain_exists(UMBRELLA_CHAIN):
        _run("-N", UMBRELLA_CHAIN)
    # Reaffirme la position 1 de FORWARD a CHAQUE application -- libvirt
    # reinsere ses propres regles de saut a chaque (re)demarrage de reseau,
    # ce qui peut techniquement repousser la notre plus bas. Verifie avant
    # d'inserer (idempotent : jamais de doublon meme appele en boucle).
    # Limite connue, documentee plutot que masquee : un `virsh net-start`
    # execute EN DEHORS d'Hyperlite entre deux appels ici pourrait, en
    # theorie, faire passer une chaine libvirt devant la notre -- rejouer
    # une regle depuis l'UI (ou redemarrer hyperlite.service, voir
    # reapply_all) suffit a reaffirmer la position 1.
    check = _run("-C", "FORWARD", "-j", UMBRELLA_CHAIN)
    if check.returncode != 0:
        _run("-I", "FORWARD", "1", "-j", UMBRELLA_CHAIN)


def _bridge_name(conn, network_name: str):
    try:
        net = conn.networkLookupByName(network_name)
    except libvirt.libvirtError:
        return None
    root = ET.fromstring(net.XMLDesc(0))
    bridge = root.find("bridge")
    return bridge.get("name") if bridge is not None else None


def _ensure_network_jump(bridge: str, chain: str):
    for flag in ("-i", "-o"):
        if _run("-C", UMBRELLA_CHAIN, flag, bridge, "-j", chain).returncode != 0:
            _run("-A", UMBRELLA_CHAIN, flag, bridge, "-j", chain)


def _remove_network_jump(bridge: str, chain: str):
    for flag in ("-i", "-o"):
        while _run("-C", UMBRELLA_CHAIN, flag, bridge, "-j", chain).returncode == 0:
            _run("-D", UMBRELLA_CHAIN, flag, bridge, "-j", chain)


def _build_rule_specs(bridge: str, config: dict):
    """Reutilise EXACTEMENT la forme de FirewallConfig/FirewallRule du
    pare-feu par VM (app/routers/vms.py, via app/core/firewall_shared.py) --
    meme UI, meme validation, seule la CIBLE change (une chaine iptables
    au lieu d'un filtre nwfilter). "in"/"out" gardent le sens du pare-feu
    par VM (relatif a la VM) : "in" = trafic ENTRANT vers les VM de ce
    reseau (le pont est la sortie du paquet cote hote, -o), "out" =
    trafic SORTANT depuis les VM (le pont est l'entree du paquet, -i)."""
    directions_map = {"in": ["-o"], "out": ["-i"], "inout": ["-i", "-o"]}
    # BUG REEL trouve en testant (ping sortant autorise mais reponse ICMP
    # jamais revenue) : sans ceci, chaque regle est evaluee sans etat --
    # autoriser le trafic SORTANT ne laisse pas automatiquement revenir sa
    # REPONSE, qui est du trafic ENTRANT distinct au sens de ce filtre.
    # Comme tout pare-feu reel (iptables lui-meme en best practice,
    # Proxmox, pfSense...), le retour d'une connexion deja autorisee doit
    # passer sans qu'il faille écrire une regle miroir manuelle pour
    # chaque protocole/port dans les deux sens.
    specs = [
        ["-i", bridge, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-o", bridge, "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
    ]
    for rule in config["rules"]:
        proto = [] if rule["protocol"] == "all" else ["-p", rule["protocol"]]
        port = ["--dport", str(rule["port"])] if rule.get("port") and rule["protocol"] in ("tcp", "udp") else []
        target = "ACCEPT" if rule["action"] == "accept" else "DROP"
        for flag in directions_map[rule["direction"]]:
            specs.append([flag, bridge, *proto, *port, "-j", target])
    default_target = "ACCEPT" if config["default_policy"] == "accept" else "DROP"
    specs.append(["-i", bridge, "-j", default_target])
    specs.append(["-o", bridge, "-j", default_target])
    return specs


def apply_network_firewall(conn, network_name: str, config: dict):
    """Reconstruit entierement la chaine dediee au reseau (flush + regles
    dans l'ordre) -- meme principe que nwfilterDefineXML pour le pare-feu
    par VM : on redefinit tout plutot que de diffuser un patch incremental,
    jamais divergent d'avec `config`. Retourne un resume, leve ValueError/
    RuntimeError sur echec (a charge de l'appelant de les traduire en
    HTTPException)."""
    bridge = _bridge_name(conn, network_name)
    if not bridge:
        raise ValueError(f"Réseau '{network_name}' introuvable ou sans pont associé (mode 'bridge' vers un pont hôte non géré par Hyperlite ?)")

    chain = _chain_name(network_name)
    _ensure_umbrella_chain()
    if not _chain_exists(chain):
        _run("-N", chain)
    _run("-F", chain)
    for spec in _build_rule_specs(bridge, config):
        result = _run("-A", chain, *spec)
        if result.returncode != 0:
            raise RuntimeError(f"iptables a refusé une règle ({' '.join(spec)}) : {result.stderr.strip()}")
    _ensure_network_jump(bridge, chain)

    with get_conn() as db:
        db.execute(
            "INSERT INTO network_firewall (network_name, default_policy, rules_json) VALUES (?, ?, ?) "
            "ON CONFLICT(network_name) DO UPDATE SET default_policy = excluded.default_policy, rules_json = excluded.rules_json",
            (network_name, config["default_policy"], json.dumps(config["rules"])),
        )
        db.commit()

    return {"pont": bridge, "regles_appliquees": len(config["rules"])}


def get_network_firewall(network_name: str) -> dict:
    with get_conn() as db:
        row = db.execute(
            "SELECT default_policy, rules_json FROM network_firewall WHERE network_name = ?", (network_name,)
        ).fetchone()
    if not row:
        return {"default_policy": "accept", "rules": []}  # rien de configure -- tout autorise, comportement par defaut
    return {"default_policy": row["default_policy"], "rules": json.loads(row["rules_json"])}


def remove_network_firewall(conn, network_name: str):
    """Appelee a la suppression d'un reseau (app/routers/network.py) --
    sans ca, le saut depuis HYPERLITENETFW et la chaine dediee resteraient
    references a un pont qui n'existe plus (inoffensif en pratique --
    iptables ne matche plus jamais rien pour un pont disparu -- mais une
    chaine orpheline qui s'accumule a chaque reseau recree finirait par
    polluer la table)."""
    bridge = _bridge_name(conn, network_name)
    chain = _chain_name(network_name)
    if bridge:
        _remove_network_jump(bridge, chain)
    if _chain_exists(chain):
        _run("-F", chain)
        _run("-X", chain)
    with get_conn() as db:
        db.execute("DELETE FROM network_firewall WHERE network_name = ?", (network_name,))
        db.commit()


def reapply_all(conn):
    """Appelee au demarrage du service (app/main.py::on_startup) -- les
    regles iptables ne survivent PAS a un redemarrage de l'HOTE
    (contrairement au nwfilter du pare-feu par VM, stocke et reapplique
    automatiquement par libvirt lui-meme) ; sans ce reapply, un pare-feu
    reseau configure avant un reboot du serveur disparaitrait
    silencieusement apres, sans que rien ne l'indique cote UI (le reseau
    parait toujours "actif", juste sans plus aucun filtrage)."""
    with get_conn() as db:
        rows = db.execute("SELECT network_name, default_policy, rules_json FROM network_firewall").fetchall()
    for row in rows:
        config = {"default_policy": row["default_policy"], "rules": json.loads(row["rules_json"])}
        try:
            apply_network_firewall(conn, row["network_name"], config)
        except Exception as e:
            print(f"[network_firewall] échec de réapplication pour '{row['network_name']}' : {e!r}", flush=True)
