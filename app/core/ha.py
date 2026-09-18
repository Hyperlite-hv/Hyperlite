"""Haute disponibilite basique (chantier 17 de la roadmap vSphere/vCenter,
2026-09-17) -- equivalent tres reduit de vSphere HA/Proxmox HA.

Scope DELIBEREMENT prudent (explique en detail dans CLAUDE.md) : rien
n'empeche un nœud "detecte hors ligne" d'etre en fait toujours vivant,
juste injoignable (simple coupure reseau, redemarrage en cours...). Sans
protection, redemarrer AUTOMATIQUEMENT une VM protegee ailleurs alors que
l'original tourne encore sur le MEME disque partage causerait une vraie
corruption de donnees (deux processus QEMU ecrivant sur le meme fichier
qcow2 en meme temps -- le pire scenario possible pour un outil cense
proteger les donnees). Hyperlite se limite donc a :
 1. DETECTER qu'un nœud portant une VM protegee est tombe (reutilise le
    poller existant, voir cluster.py::_poll_nodes) et le signaler
    clairement (alerte visible + entree d'audit).
 2. FENCING best-effort par SSH (backlog 2026-09-18, `_attempt_ssh_fence()`
    ci-dessous) : AVANT toute recuperation, tente de confirmer/forcer
    l'arret du processus qemu original en se connectant directement en
    SSH au nœud "hors ligne" -- PAS via libvirt (c'est justement la
    connexion libvirt qui a echoue, voir cluster.py::test_node_connection ;
    tres souvent parce que libvirtd a plante alors que le processus qemu,
    lui, continue de tourner independamment -- exactement le scenario
    reellement rencontre en testant ce chantier). Reste un fencing
    "faible" (pas de coupure d'alimentation IPMI/PDU, aucune carte de
    gestion a distance sur ce materiel) : si le nœud est AUSSI injoignable
    en SSH, le fencing echoue et c'est note comme tel, mais **ne bloque
    PAS** la recuperation -- le verrou d'ecriture natif de QEMU
    (Failed to get 'write' lock...) reste le filet de securite ultime,
    deja confirme efficace en testant (voir plus bas).
 3. Laisser un ADMIN HUMAIN -- qui a un contexte que Hyperlite n'a pas
    (le nœud redemarre-t-il juste ? est-il vraiment mort ?) -- declencher
    la recuperation en un clic. Jamais automatique, meme apres un fencing
    SSH reussi (decision explicite d'Antho : le controle humain reste
    avant toute action qui change l'etat du cluster).

Protection EXIGE que tous les disques de la VM soient deja sur un pool de
stockage partage (chantier 26, netfs) : sans ca, aucune garantie que le
disque soit seulement lisible depuis un autre nœud en cas de bascule.
"""
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import libvirt

from app.core.database import get_conn
from app.core.audit import log_action
from app.core.libvirt_utils import open_conn, uses_shared_storage


def _now():
    return datetime.now(timezone.utc).isoformat()


def _portable_xml(raw_xml):
    """Normalise le XML d'un domaine ACTIF (domain.XMLDesc(0)) avant de le
    mettre en cache pour une recuperation future potentielle sur un AUTRE
    nœud -- BUGS REELS trouves en testant une vraie recuperation
    kvm-lab <-> serveur-antho (versions QEMU differentes, meme materiel
    Intel) :
    1. Le type de machine ('machine=pc-i440fx-10.0') est resolu par
       libvirt vers une version CONCRETE au demarrage -- l'emulateur plus
       ancien de l'autre nœud ne la reconnait pas ('unsupported
       configuration: ... does not support machine type'). Ramene a
       l'alias generique 'pc' (meme principe que le 'machine=pc' utilise
       a la creation dans vm_builder.py -- laisse CHAQUE hote choisir la
       version concrete qu'il supporte).
    2. Le CPU est deja resolu en 'custom'/'exact' avec des dizaines de
       'feature policy=require' specifiques au CPU du nœud qui faisait
       tourner la VM -- echoue si l'autre nœud n'a pas EXACTEMENT les
       memes (constate reellement : 'Host CPU does not provide required
       features'). Ramene a 'host-model' (comportement par defaut de
       vm_builder.py) -- sacrifie l'optimisation de migrabilite du
       chantier 27 pour maximiser les chances qu'une recuperation
       D'URGENCE reussisse, ce qui est le seul but de ce cache."""
    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return raw_xml  # improbable (XML vient de libvirt lui-meme) -- ne bloque pas le cache sur cette normalisation best-effort

    type_el = root.find("os/type")
    if type_el is not None and type_el.get("machine"):
        type_el.set("machine", "pc")

    cpu_el = root.find("cpu")
    if cpu_el is not None:
        root.remove(cpu_el)
    ET.SubElement(root, "cpu", mode="host-model")

    return ET.tostring(root, encoding="unicode")


def _conn_key(node_label):
    """'kvm-lab' (convention frontend pour l'hote local, voir
    fetchNodes()) -> None (convention backend, voir open_conn()) -- jamais
    une ligne de la table `nodes`."""
    return None if node_label in (None, "kvm-lab") else node_label


def list_protected():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM ha_protected_vms ORDER BY vm_name").fetchall()
    return [dict(r) for r in rows]


def get_protected(vm_name):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ha_protected_vms WHERE vm_name = ?", (vm_name,)).fetchone()
    return dict(row) if row else None


def enable_protection(vm_name, node, username):
    node_label = node or "kvm-lab"
    conn = open_conn(_conn_key(node_label))
    try:
        try:
            domain = conn.lookupByName(vm_name)
        except libvirt.libvirtError:
            raise RuntimeError(f"VM '{vm_name}' introuvable sur le nœud '{node_label}'")

        # src_conn == dest_conn : reutilise uses_shared_storage() (pensee
        # pour comparer DEUX nœuds lors d'une migration) pour repondre a
        # une question plus simple ici -- "ce disque est-il sur UN pool
        # netfs, tout court" -- sans avoir besoin d'un second nœud candidat.
        if not uses_shared_storage(conn, conn, domain):
            raise RuntimeError(
                "Protection HA impossible : au moins un disque de cette VM n'est pas sur un pool de "
                "stockage partagé (NFS, chantier 26). Déplacez son disque sur un pool partagé d'abord."
            )

        now = _now()
        with get_conn() as db:
            db.execute(
                "INSERT INTO ha_protected_vms (vm_name, node, domain_xml, enabled_by, enabled_at, last_synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(vm_name) DO UPDATE SET node=excluded.node, domain_xml=excluded.domain_xml, last_synced_at=excluded.last_synced_at",
                (vm_name, node_label, _portable_xml(domain.XMLDesc(0)), username, now, now),
            )
            db.commit()
        log_action(username, "ha_enable", vm_name, "succes", f"nœud {node_label}")
    finally:
        conn.close()


def disable_protection(vm_name, username):
    with get_conn() as db:
        db.execute("DELETE FROM ha_protected_vms WHERE vm_name = ?", (vm_name,))
        db.commit()
    log_action(username, "ha_disable", vm_name, "succes")


def sync_protected_vms():
    """Appele periodiquement (voir cluster.py::_poll_nodes, meme boucle,
    pas de thread dedie de plus) PENDANT que chaque nœud protege est
    joignable : rafraichit le cache domain_xml (seul moyen d'avoir une
    configuration a redefinir ailleurs le jour ou ce nœud tombe VRAIMENT
    en panne -- on ne peut plus lui demander son XML une fois injoignable)
    et desactive automatiquement la protection si le stockage n'est plus
    partage (config changee entretemps) -- mieux vaut une protection
    desactivee proprement, avec une trace claire dans l'audit, qu'une
    protection qui mentirait silencieusement sur ses garanties."""
    for row in list_protected():
        try:
            conn = open_conn(_conn_key(row["node"]))
        except Exception:
            continue  # nœud injoignable maintenant : rien a resynchroniser, le cache existant reste la derniere version connue valable
        try:
            try:
                domain = conn.lookupByName(row["vm_name"])
            except libvirt.libvirtError:
                disable_protection(row["vm_name"], "system")
                log_action("system", "ha_auto_disable", row["vm_name"], "echec", "VM introuvable sur le nœud protégé")
                continue
            if not uses_shared_storage(conn, conn, domain):
                disable_protection(row["vm_name"], "system")
                log_action("system", "ha_auto_disable", row["vm_name"], "echec", "stockage plus partagé")
                continue
            with get_conn() as db:
                db.execute(
                    "UPDATE ha_protected_vms SET domain_xml = ?, last_synced_at = ? WHERE vm_name = ?",
                    (_portable_xml(domain.XMLDesc(0)), _now(), row["vm_name"]),
                )
                db.commit()
        finally:
            conn.close()


def alert_for_down_node(node_name):
    """Appele par cluster.py::_poll_nodes des qu'un nœud PASSE a l'etat
    'hors_ligne' -- signale chaque VM protegee qui s'y trouvait, SANS
    tenter de fencing ici (le fencing SSH, voir _attempt_ssh_fence()
    plus bas, n'a lieu qu'au moment ou un admin declenche reellement
    recover() -- pas a chaque cycle de detection, qui serait bien plus
    frequent et bruyant pour un gain nul tant que personne ne recupere)."""
    for row in list_protected():
        if row["node"] != node_name:
            continue
        log_action(
            "system", "ha_alert", row["vm_name"], "echec",
            f"Nœud '{node_name}' hors ligne — VM protégée, récupération manuelle disponible (onglet HA)",
        )


def _attempt_ssh_fence(node_name, vm_name):
    """Fencing best-effort par SSH (backlog 2026-09-18, voir docstring du
    module). PAS de libvirt ici -- le nœud est detecte "hors ligne" par un
    echec de CETTE connexion precise (cluster.py::test_node_connection),
    donc la retenter n'apporterait rien ; on interroge directement le
    processus au niveau du noyau. Repere le(s) PID qemu de cette VM par sa
    ligne de commande : libvirt lance toujours qemu avec `-name
    guest=<nom>,...`, un motif stable quelle que soit la version de
    QEMU/libvirt.

    Retourne (fenced: bool, detail: str). fenced=True veut dire "confirme
    qu'aucun processus qemu de cette VM ne tourne plus la-bas" (kill
    reussi OU deja absent) -- fenced=False veut dire fencing impossible
    (nœud injoignable meme en SSH, ou kill echoue), mais NE DOIT JAMAIS
    bloquer recover() : le verrou d'ecriture natif de QEMU reste le filet
    de securite ultime dans ce cas (deja confirme efficace en testant le
    chantier 17 lui-meme)."""
    from app.core.cluster import get_node, get_cluster_private_key_path

    node = get_node(node_name)
    if not node:
        return False, "nœud introuvable dans la table `nodes`"

    key_path = str(get_cluster_private_key_path())
    ssh_opts = [
        "-i", key_path, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=8",
    ]
    ssh_target = f"{node['ssh_user']}@{node['hostname']}"
    # BUG REEL trouve en testant : `pgrep -af` matchait sa PROPRE
    # invocation (sshd execute la commande distante via un `bash -c
    # "pgrep -af 'guest=<nom>,' | ..."` dont la ligne de commande contient
    # ELLE-MEME le motif recherche) -- un faux PID "trouve" a chaque appel,
    # y compris apres un kill reellement reussi (confirme separement via
    # `virsh domstate` : la VM etait bien eteinte alors que la
    # "reverification" pretendait encore un PID actif). Corrige en filtrant
    # sur le nom du binaire (2e champ, juste apres le PID) : seul le
    # veritable processus qemu commence par 'qemu-system', jamais
    # 'bash'/'pgrep' qui s'auto-matchent.
    find_cmd = f"pgrep -af 'guest={vm_name},' | awk '$2 ~ /qemu-system/ {{print $1}}'"

    def _find_pids():
        try:
            r = subprocess.run(
                ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), ssh_target, find_cmd],
                capture_output=True, text=True, timeout=12,
            )
        except (subprocess.SubprocessError, OSError) as e:
            return None, f"SSH injoignable : {e}"
        # pgrep renvoie 1 (pas d'erreur) quand rien ne correspond -- seul
        # un code >1 indique un vrai probleme (SSH/commande distante).
        if r.returncode not in (0, 1):
            return None, f"SSH injoignable ou erreur : {(r.stderr or '').strip()[:200]}"
        return [p for p in r.stdout.split() if p.isdigit()], None

    pids, err = _find_pids()
    if pids is None:
        return False, err
    if not pids:
        return True, "aucun processus qemu trouvé pour cette VM (déjà arrêté)"

    subprocess.run(
        ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), ssh_target, "kill -9 " + " ".join(pids)],
        capture_output=True, text=True, timeout=12,
    )
    remaining, err2 = _find_pids()
    if remaining is None:
        return False, f"kill envoyé (PID {','.join(pids)}) mais vérification impossible : {err2}"
    if remaining:
        return False, f"processus toujours actif après kill (PID {','.join(remaining)})"
    return True, f"processus qemu tué avec succès (PID {','.join(pids)})"


def recover(vm_name, target_node, username):
    row = get_protected(vm_name)
    if not row:
        raise RuntimeError(f"'{vm_name}' n'est pas une VM protégée par la HA")
    if row["node"] == target_node:
        raise RuntimeError("Le nœud de destination doit être différent du nœud protégé actuel")
    if not row["domain_xml"]:
        raise RuntimeError("Aucune configuration en cache pour cette VM — jamais synchronisée avec succès")

    # Fencing best-effort AVANT toute action (backlog 2026-09-18, voir
    # docstring du module) -- journalise systematiquement, que ca reussisse
    # ou non : l'admin doit pouvoir voir si la mort du processus original a
    # ete reellement confirmee ou si la recuperation ne s'appuie que sur le
    # verrou d'ecriture natif de QEMU (deja un filet de securite reel,
    # confirme en testant, mais moins fort qu'une confirmation active).
    fenced, fence_detail = _attempt_ssh_fence(row["node"], vm_name)
    log_action(
        username, "ha_fence", vm_name, "succes" if fenced else "echec",
        f"Nœud '{row['node']}' : {fence_detail}",
    )

    conn = open_conn(_conn_key(target_node))
    try:
        try:
            conn.lookupByName(vm_name)
            raise RuntimeError(f"Une VM '{vm_name}' existe déjà sur '{target_node}' — risque de conflit, récupération refusée")
        except libvirt.libvirtError:
            pass

        try:
            new_domain = conn.defineXML(row["domain_xml"])
            new_domain.create()
        except libvirt.libvirtError as e:
            raise RuntimeError(f"Échec de la récupération : {e}")

        with get_conn() as db:
            db.execute(
                "UPDATE ha_protected_vms SET node = ?, domain_xml = ?, last_synced_at = ? WHERE vm_name = ?",
                (target_node, _portable_xml(new_domain.XMLDesc(0)), _now(), vm_name),
            )
            db.commit()
        log_action(username, "ha_recover", vm_name, "succes", f"{row['node']} -> {target_node}")
    finally:
        conn.close()
