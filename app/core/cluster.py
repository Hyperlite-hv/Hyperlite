"""Gestion multi-nœuds (chantier 15 de la roadmap vSphere/vCenter,
2026-09-13) -- equivalent de vCenter pilotant plusieurs hotes ESXi depuis
une seule interface. Pose les bases d'un futur clustering (PAS du vMotion/
DRS, hors scope, voir chantier 8).

Decision d'architecture (options presentees comme demande avant de choisir) :

  A) Connexion libvirt distante directe (qemu+ssh://) -- RETENUE.
     + Aucun agent a deployer/maintenir sur les noeuds distants : le seul
       prerequis est un demon SSH + libvirt/QEMU-KVM deja en place (souvent
       deja le cas sur un hote destine a heberger des VM).
     + Reutilise integralement le code existant : chaque routeur appelle
       deja open_conn(), etendre cette seule fonction avec un parametre
       node_name (voir libvirt_utils.py) suffit a rendre CE QUI EXISTE DEJA
       multi-noeuds-capable, sans reecrire vms.py/network.py/storage.py.
     - Sensible a la latence reseau (chaque appel libvirt fait un aller-
       retour SSH) -- acceptable pour du pilotage/consultation, pourrait
       devenir genant pour des operations tres frequentes (polling metriques
       serre) sur un lien lent.
     - Le processus hyperlite lui-meme doit joindre le noeud en SSH pour
       CHAQUE operation (pas de mise en cache de connexion pour l'instant
       dans cette premiere version) -- une coupure reseau fait echouer
       l'operation en cours, proprement (voir _describe/health check), pas
       un crash.

  B) Agent Hyperlite deploye sur chaque noeud distant (API locale consommee
     par le noeud principal) -- ECARTEE pour cette taille de projet.
     + Plus robuste/decouple : l'agent peut mettre en cache, retenter,
       exposer une API deja pensee pour du distant plutot que de detourner
       une API pensee pour du local.
     + Latence eventuellement meilleure (traitement local, reponse condensee).
     - Un second binaire a construire, versionner, deployer et mettre a jour
       sur CHAQUE noeud (rejoint le sujet du chantier 7 -- multiplierait le
       probleme de mise a jour par le nombre de noeuds).
     - Duplique une bonne partie de la logique deja ecrite cote "local"
       (app/core/*, app/routers/*) plutot que de la reutiliser.

  Verdict : (A) est le choix pragmatique pour la taille actuelle
  d'Hyperlite -- zero nouveau composant a deployer, reutilisation maximale
  du code existant. A reconsiderer si la latence SSH devient un vrai
  probleme en usage reel, ou si le nombre de noeuds grandit beaucoup
  (dizaines) au point ou la robustesse d'un agent deviendrait rentable.
"""
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import libvirt

from app.core.audit import log_action
from app.core.database import get_conn
from app.core.vm_builder import PROJDIR

CLUSTER_SSH_KEY_DIR = PROJDIR / "data" / "ssh"
POLL_INTERVAL_S = 60


def _ensure_cluster_keypair():
    """Cle SSH DEDIEE au cluster, distincte de la cle d'automatisation VM
    (hyperlite_automation) -- une cle qui ouvre un acces root sur d'autres
    HOTES physiques ne doit jamais etre la meme que celle installee dans des
    VM invitees potentiellement moins sensibles."""
    CLUSTER_SSH_KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv = CLUSTER_SSH_KEY_DIR / "hyperlite_cluster"
    pub = CLUSTER_SSH_KEY_DIR / "hyperlite_cluster.pub"
    if not priv.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-C", "hyperlite-cluster"],
            check=True, capture_output=True, text=True,
        )
        priv.chmod(0o600)
    return priv, pub


def get_cluster_pubkey():
    _, pub = _ensure_cluster_keypair()
    return pub.read_text().strip()


def get_cluster_private_key_path():
    priv, _ = _ensure_cluster_keypair()
    return priv


def build_libvirt_uri(node):
    """qemu+ssh://<user>@<host>:<port>/system, avec la cle dediee du cluster
    et sans verification stricte de known_hosts -- meme compromis deja
    accepte pour le terminal SSH web (StrictHostKeyChecking=no) : simplicite
    d'ajout d'un noeud vs risque MITM sur un reseau interne de confiance.
    A durcir (verification explicite de l'empreinte) si Hyperlite est un
    jour expose au-dela d'un LAN de confiance."""
    key_path = get_cluster_private_key_path()
    return (
        f"qemu+ssh://{node['ssh_user']}@{node['hostname']}:{node['ssh_port']}/system"
        f"?keyfile={key_path}&no_verify=1&sshauth=privkey"
    )


def get_node(name):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_nodes():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM nodes ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def test_node_connection(hostname, ssh_user, ssh_port):
    """Tente une vraie connexion libvirt distante et verifie qu'il s'agit
    bien d'un hote QEMU/KVM -- pas juste "le port SSH repond", comme deja
    fait pour la verification de bout en bout du Kickstart (chantier 12)."""
    fake_node = {"hostname": hostname, "ssh_user": ssh_user, "ssh_port": ssh_port}
    uri = build_libvirt_uri(fake_node)
    try:
        conn = libvirt.openReadOnly(uri)
    except libvirt.libvirtError as e:
        return False, str(e)
    if conn is None:
        return False, "Connexion refusée (raison inconnue)"
    try:
        hv_type = conn.getType()
        hostname_reelle = conn.getHostname()
        if hv_type != "QEMU":
            return False, f"Hyperviseur '{hv_type}' détecté, QEMU/KVM attendu"
        return True, hostname_reelle
    finally:
        conn.close()


REVERSE_KEY_DIR = CLUSTER_SSH_KEY_DIR / "reverse"
REVERSE_REMOTE_DIR = "/root/.hyperlite-reverse"
REVERSE_KEY_TAG = "hyperlite-reverse"  # marqueur en fin de ligne authorized_keys, pour retrouver/nettoyer nos propres entrees


def _authorized_keys_path():
    return Path("/root/.ssh/authorized_keys")


def _run_ssh(node, args, timeout=15):
    key_path = str(get_cluster_private_key_path())
    ssh_opts = ["-i", key_path, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    target = f"{node['ssh_user']}@{node['hostname']}"
    return subprocess.run(
        ["ssh", *ssh_opts, "-p", str(node["ssh_port"]), target, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def ensure_reverse_trust(node):
    """Confiance SSH DANS LE SENS INVERSE (nœud distant -> kvm-lab),
    necessaire pour qu'une migration a chaud INITIEE depuis un nœud
    distant puisse ramener une VM vers kvm-lab -- la migration peer-to-
    peer est toujours initiee par le libvirtd SOURCE, qui doit pouvoir se
    connecter LUI-MEME vers la destination (voir la limite connue
    documentee dans app/routers/vms.py::migrate_vm avant ce correctif).
    Jusqu'ici, seule la confiance kvm-lab -> nœud distant existait (cle
    UNIQUE partagee par tous les nœuds, `hyperlite_cluster`).

    Design volontairement DIFFERENT de cette cle unique : reutiliser la
    MEME cle partagee pour le sens inverse donnerait a CHAQUE nœud
    compromis un acces root direct a kvm-lab (qui porte la cle de
    signature GPG, la base de donnees complete, tous les secrets) --
    inacceptable. Genere donc une paire de cles DEDIEE A CE NŒUD, poussee
    UNIQUEMENT sur ce nœud (jamais partagee), autorisee sur kvm-lab avec
    une restriction `from=` a l'adresse de CE nœud precis -- une cle
    volee sur un nœud ne peut etre reutilisee que depuis l'adresse de ce
    meme nœud, pas depuis n'importe ou.

    Best-effort total : appelee automatiquement a l'enregistrement d'un
    nœud, mais un echec ici ne doit JAMAIS faire echouer l'enregistrement
    lui-meme (la direction kvm-lab -> nœud, largement la plus utilisee,
    reste fonctionnelle independamment)."""
    REVERSE_KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    priv = REVERSE_KEY_DIR / f"{node['name']}_ed25519"
    pub = REVERSE_KEY_DIR / f"{node['name']}_ed25519.pub"
    if not priv.exists():
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-C", f"hyperlite-reverse-{node['name']}"],
            check=True, capture_output=True, text=True,
        )
        priv.chmod(0o600)
    pub_line = pub.read_text().strip()

    # 1) Pousse la cle PRIVEE sur le nœud distant (via la confiance
    # EXISTANTE kvm-lab -> nœud) -- c'est LA-BAS que le libvirtd source en
    # aura besoin au moment de la migration.
    mkdir_r = _run_ssh(node, ["mkdir", "-p", REVERSE_REMOTE_DIR])
    if mkdir_r.returncode != 0:
        raise RuntimeError(f"Impossible de créer {REVERSE_REMOTE_DIR} sur {node['name']} : {mkdir_r.stderr.strip()}")
    key_path = str(get_cluster_private_key_path())
    ssh_opts = ["-i", key_path, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    scp_r = subprocess.run(
        ["scp", *ssh_opts, "-P", str(node["ssh_port"]), str(priv), f"{node['ssh_user']}@{node['hostname']}:{REVERSE_REMOTE_DIR}/id_ed25519"],
        capture_output=True, text=True, timeout=15,
    )
    if scp_r.returncode != 0:
        raise RuntimeError(f"Échec de la copie de la clé sur {node['name']} : {scp_r.stderr.strip()}")
    _run_ssh(node, ["chmod", "600", f"{REVERSE_REMOTE_DIR}/id_ed25519"])

    # 2) Autorise la cle PUBLIQUE sur kvm-lab, restreinte a l'adresse de ce
    # nœud precis -- idempotent (ne duplique pas si deja present, ex. un
    # second appel apres une modification du nœud).
    auth_path = _authorized_keys_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    existing = auth_path.read_text() if auth_path.exists() else ""
    marker = f"{REVERSE_KEY_TAG}-{node['name']}"
    if marker not in existing:
        line = f'from="{node["hostname"]}",no-port-forwarding,no-X11-forwarding,no-agent-forwarding {pub_line} {marker}\n'
        with open(auth_path, "a") as f:
            f.write(line)
        auth_path.chmod(0o600)


def revoke_reverse_trust(node_name):
    """Nettoyage best-effort a la suppression d'un nœud (voir remove_node) :
    retire l'entree authorized_keys correspondante sur kvm-lab (toujours
    possible, fichier local) et la paire de cles locale -- ne tente PAS de
    joindre le nœud distant pour supprimer la cle privee la-bas (il peut
    deja etre injoignable, la raison meme pour laquelle on le retire) : la
    cle laissee sur le nœud devient simplement inutile des que l'entree
    authorized_keys correspondante n'existe plus sur kvm-lab."""
    marker = f"{REVERSE_KEY_TAG}-{node_name}"
    auth_path = _authorized_keys_path()
    if auth_path.exists():
        lines = [l for l in auth_path.read_text().splitlines(keepends=True) if marker not in l]
        auth_path.write_text("".join(lines))
    (REVERSE_KEY_DIR / f"{node_name}_ed25519").unlink(missing_ok=True)
    (REVERSE_KEY_DIR / f"{node_name}_ed25519.pub").unlink(missing_ok=True)


def get_reverse_key_remote_path():
    """Chemin (SUR LE NŒUD DISTANT, pas sur kvm-lab) de la cle privee
    poussee par ensure_reverse_trust() -- utilise par migrate_vm() pour
    construire l'URI de destination quand kvm-lab est la cible d'une
    migration initiee par un nœud distant."""
    return f"{REVERSE_REMOTE_DIR}/id_ed25519"


def register_node(name, hostname, ssh_user, ssh_port, username):
    ok, message = test_node_connection(hostname, ssh_user, ssh_port)
    if not ok:
        raise RuntimeError(
            f"Connexion impossible : {message}. Vérifiez que la clé publique du cluster est "
            f"installée dans ~{ssh_user}/.ssh/authorized_keys sur {hostname} (GET /nodes/cluster-pubkey "
            f"pour la récupérer) et que libvirtd y tourne."
        )
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO nodes (name, hostname, ssh_user, ssh_port, statut, derniere_verification, added_at) "
                "VALUES (?, ?, ?, ?, 'en_ligne', ?, ?)",
                (name, hostname, ssh_user, ssh_port, now, now),
            )
            conn.commit()
        except Exception:
            raise RuntimeError(f"Un nœud '{name}' existe déjà")
    log_action(username, "register_node", name, "succes", f"{ssh_user}@{hostname}:{ssh_port}")
    node = get_node(name)
    # Best-effort (voir docstring d'ensure_reverse_trust) -- la migration
    # nœud distant -> kvm-lab reste une fonctionnalite secondaire, ne doit
    # jamais faire echouer l'enregistrement du nœud lui-meme.
    try:
        ensure_reverse_trust(node)
    except Exception as e:
        log_action(username, "register_node", name, "succes", f"Confiance SSH inverse non établie (migration nœud->kvm-lab indisponible) : {e}")
    return node


def remove_node(name, username):
    with get_conn() as conn:
        conn.execute("DELETE FROM nodes WHERE name = ?", (name,))
        conn.commit()
    revoke_reverse_trust(name)
    log_action(username, "remove_node", name, "succes")


def node_summary(node_name):
    """CPU/RAM/stockage/VM du noeud -- meme forme que GET /dashboard local
    (app/routers/dashboard.py), pour que le front puisse reutiliser le meme
    rendu quel que soit le noeud affiche."""
    from app.core.libvirt_utils import open_conn, ensure_default_pool

    conn = open_conn(node_name)
    try:
        domains = conn.listAllDomains()
        active = sum(1 for d in domains if d.isActive())
        try:
            pool = ensure_default_pool(conn)
            pool.refresh(0)
            _, capacity, allocation, available = pool.info()
        except libvirt.libvirtError:
            capacity = available = None
        return {
            "hostname": conn.getHostname(),
            "connecte": conn.isAlive() == 1,
            "vms_actives": active,
            "vms_arretees": len(domains) - active,
            "stockage_capacite_go": round(capacity / (1024 ** 3), 1) if capacity else None,
            "stockage_disponible_go": round(available / (1024 ** 3), 1) if available else None,
        }
    finally:
        conn.close()


def _poll_nodes():
    while True:
        try:
            with get_conn() as conn:
                nodes = conn.execute("SELECT * FROM nodes").fetchall()
            for node in nodes:
                ok, _ = test_node_connection(node["hostname"], node["ssh_user"], node["ssh_port"])
                new_statut = "en_ligne" if ok else "hors_ligne"
                with get_conn() as conn:
                    prev = conn.execute("SELECT statut FROM nodes WHERE id = ?", (node["id"],)).fetchone()
                    conn.execute(
                        "UPDATE nodes SET statut = ?, derniere_verification = ? WHERE id = ?",
                        (new_statut, datetime.now(timezone.utc).isoformat(), node["id"]),
                    )
                    conn.commit()
                if prev and prev["statut"] != new_statut:
                    log_action("system", "node_statut_change", node["name"], "succes" if ok else "echec", new_statut)
                    if new_statut == "hors_ligne":
                        # HA (chantier 17) : signale les VM protegees de ce
                        # nœud DES la detection -- import tardif, evite un
                        # cycle (ha.py importe deja depuis libvirt_utils.py).
                        from app.core.ha import alert_for_down_node
                        try:
                            alert_for_down_node(node["name"])
                        except Exception as ha_exc:
                            print(f"[cluster] alerte HA échouée pour {node['name']} : {ha_exc!r}", flush=True)
            # HA : resynchronise le cache des VM protegees pendant que leur
            # nœud est joignable -- meme cadence que le poll des nœuds
            # (POLL_INTERVAL_S), pas besoin d'une boucle dediee separee.
            try:
                from app.core.ha import sync_protected_vms
                sync_protected_vms()
            except Exception as ha_exc:
                print(f"[cluster] resynchronisation HA échouée : {ha_exc!r}", flush=True)
        except Exception as e:
            print(f"[cluster] poll échoué : {e!r}", flush=True)
        time.sleep(POLL_INTERVAL_S)


def start_node_poller():
    thread = threading.Thread(target=_poll_nodes, daemon=True)
    thread.start()
    return thread
