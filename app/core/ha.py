"""Haute disponibilite basique (chantier 17 de la roadmap vSphere/vCenter,
2026-09-17) -- equivalent tres reduit de vSphere HA/Proxmox HA.

Scope DELIBEREMENT prudent (explique en detail dans CLAUDE.md) : ce
mecanisme n'a AUCUN fencing/STONITH -- rien n'empeche un nœud "detecte hors
ligne" d'etre en fait toujours vivant, juste injoignable (simple coupure
reseau, redemarrage en cours...). Sans fencing, redemarrer
AUTOMATIQUEMENT une VM protegee ailleurs alors que l'original tourne
encore sur le MEME disque partage causerait une vraie corruption de
donnees (deux processus QEMU ecrivant sur le meme fichier qcow2 en meme
temps -- le pire scenario possible pour un outil cense proteger les
donnees). Hyperlite se limite donc a :
 1. DETECTER qu'un nœud portant une VM protegee est tombe (reutilise le
    poller existant, voir cluster.py::_poll_nodes) et le signaler
    clairement (alerte visible + entree d'audit).
 2. Laisser un ADMIN HUMAIN -- qui a un contexte que Hyperlite n'a pas
    (le nœud redemarre-t-il juste ? est-il vraiment mort ?) -- declencher
    la recuperation en un clic. Jamais automatique.

Protection EXIGE que tous les disques de la VM soient deja sur un pool de
stockage partage (chantier 26, netfs) : sans ca, aucune garantie que le
disque soit seulement lisible depuis un autre nœud en cas de bascule.
"""
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
    'hors_ligne' -- signale chaque VM protegee qui s'y trouvait, SANS RIEN
    FAIRE D'AUTOMATIQUE (voir docstring du module : pas de fencing)."""
    for row in list_protected():
        if row["node"] != node_name:
            continue
        log_action(
            "system", "ha_alert", row["vm_name"], "echec",
            f"Nœud '{node_name}' hors ligne — VM protégée, récupération manuelle disponible (onglet HA)",
        )


def recover(vm_name, target_node, username):
    row = get_protected(vm_name)
    if not row:
        raise RuntimeError(f"'{vm_name}' n'est pas une VM protégée par la HA")
    if row["node"] == target_node:
        raise RuntimeError("Le nœud de destination doit être différent du nœud protégé actuel")
    if not row["domain_xml"]:
        raise RuntimeError("Aucune configuration en cache pour cette VM — jamais synchronisée avec succès")

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
