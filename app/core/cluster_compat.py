"""Diagnostic de compatibilite de cluster (mandat portabilite, chantier 6).

Repond, AVANT d'agir, a "cette VM peut-elle migrer vers ce nœud ?" et "ces
deux nœuds sont-ils compatibles ?", avec des messages exploitables plutot
qu'une erreur opaque de libvirt en plein milieu d'une migration.

Chaque controle renvoie {id, statut, message, action?} avec statut
ok / warning / blocking (meme forme que app/core/preflight.py, dont on
reutilise summarize()). Un controle qui ne peut pas s'executer ne leve
jamais : il devient un `warning` "verification impossible" -- degradation
controlee, jamais un faux blocage ni un echec global.

Fonctions pures sur des connexions libvirt deja ouvertes (local ou
qemu+ssh://), donc testables avec de faux objets de connexion."""
import xml.etree.ElementTree as ET

import libvirt

from app.core.preflight import summarize, OK, WARNING, BLOCKING


def _c(id_, statut, message, action=None):
    d = {"id": id_, "statut": statut, "message": message}
    if action:
        d["action"] = action
    return d


def _ver(v):
    return f"{v // 1000000}.{(v // 1000) % 1000}.{v % 1000}"


def _safe(id_, fn):
    """Execute un controle ; toute exception devient un avertissement."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 -- jamais d'echec global
        return [_c(id_, WARNING, f"Vérification impossible ({type(e).__name__} : {str(e)[:120]})")]


def _caps(conn):
    return ET.fromstring(conn.getCapabilities())


def _host_arch(caps):
    return caps.findtext("host/cpu/arch")


def _machine_types(caps, arch):
    """Ensemble des types de machine supportes (noms ET alias canoniques)."""
    out = set()
    for guest in caps.findall("guest"):
        a = guest.find("arch")
        if a is None or a.get("name") != arch:
            continue
        for m in a.findall("machine"):
            if m.text:
                out.add(m.text.strip())
            if m.get("canonical"):
                out.add(m.get("canonical"))
    return out


def _has_kvm(caps):
    return any(d.get("type") == "kvm" for g in caps.findall("guest") for d in g.findall("arch/domain"))


# --- Controles entre deux hotes -----------------------------------------

def check_pair(src, dst):
    """Compatibilite generale source -> destination, independante d'une VM."""
    checks = []

    def arch():
        sa, da = _host_arch(_caps(src)), _host_arch(_caps(dst))
        if sa == da:
            return [_c("architecture", OK, f"Même architecture ({sa})")]
        return [_c("architecture", BLOCKING, f"Architectures différentes : {sa} -> {da}. Une VM ne peut pas migrer entre architectures.")]

    def versions():
        out = []
        for label, gs in (("QEMU", lambda c: c.getVersion()), ("libvirt", lambda c: c.getLibVersion())):
            sv, dv = gs(src), gs(dst)
            if dv >= sv:
                out.append(_c(f"version_{label.lower()}", OK, f"{label} {_ver(sv)} -> {_ver(dv)} (destination identique ou plus récente)"))
            else:
                out.append(_c(f"version_{label.lower()}", WARNING,
                              f"{label} de la destination ({_ver(dv)}) plus ancien que la source ({_ver(sv)}) : migration vers une version antérieure non garantie",
                              action=f"mettre à jour {label} sur la destination"))
        return out

    def kvm():
        sk, dk = _has_kvm(_caps(src)), _has_kvm(_caps(dst))
        if dk:
            return [_c("kvm", OK, "KVM disponible sur la destination")]
        if sk:
            return [_c("kvm", BLOCKING, "KVM indisponible sur la destination (virtualisation matérielle absente ou désactivée)",
                       action="activer VT-x/AMD-V (ou l'imbrication) sur la destination")]
        return [_c("kvm", WARNING, "KVM indisponible des deux côtés (émulation logicielle)")]

    def cpu():
        host_cpu = _caps(src).find("host/cpu")
        if host_cpu is None:
            return [_c("cpu", WARNING, "CPU source non lisible")]
        return [_cpu_result("cpu", dst, ET.tostring(host_cpu, encoding="unicode"), "CPU physique de la source")]

    for id_, fn in (("architecture", arch), ("versions", versions), ("kvm", kvm), ("cpu", cpu)):
        checks += _safe(id_, fn)
    return checks


def _cpu_result(id_, dst, cpu_xml, what):
    try:
        r = dst.compareCPU(cpu_xml, 0)
    except libvirt.libvirtError as e:
        if "Unknown CPU model" in str(e):
            # Cas REEL rencontre entre deux machines aux versions QEMU/libvirt
            # differentes (voir CLAUDE.md, chantier 27) : ni un blocage prouve
            # ni une compatibilite -- la cible ne connait pas le modele.
            return _c(id_, WARNING, f"{what} : modèle inconnu de la destination ({str(e).split('Unknown CPU model')[-1].strip()}) -- bases de modèles CPU différentes, compatibilité non démontrable",
                      action="utiliser un CPU générique (ex. qemu64 sans svm/vmx) pour les VM destinées à migrer entre ces nœuds")
        raise
    if r == libvirt.VIR_CPU_COMPARE_INCOMPATIBLE:
        return _c(id_, BLOCKING, f"{what} incompatible avec le CPU de la destination : la VM ne pourra pas s'y exécuter/migrer",
                  action="utiliser un CPU générique (ex. qemu64) pour cette VM, ou une destination plus récente")
    return _c(id_, OK, f"{what} compatible avec la destination")


# --- Controles specifiques a une VM -------------------------------------

def _netfs_pools(conn):
    """{(hote, repertoire source): chemin de montage local} des pools NFS."""
    out = {}
    for pool in conn.listAllStoragePools():
        root = ET.fromstring(pool.XMLDesc(0))
        if root.get("type") != "netfs":
            continue
        host_el, dir_el = root.find("source/host"), root.find("source/dir")
        host = host_el.get("name") if host_el is not None else None
        directory = dir_el.get("path") if dir_el is not None else None
        target = root.findtext("target/path")
        if host and directory and target:
            out[(host, directory)] = target
    return out


def check_vm_migration(src, dst, domain):
    """Controles pair-a-pair + ceux propres a la configuration de la VM."""
    checks = check_pair(src, dst)
    xml = ET.fromstring(domain.XMLDesc(0))
    name = domain.name()

    def state():
        if domain.isActive():
            return [_c("etat_vm", OK, "VM active (migration à chaud possible)")]
        return [_c("etat_vm", BLOCKING, "VM arrêtée : la migration à chaud exige une VM active",
                   action="démarrer la VM, ou utiliser l'export/import de disque")]

    def exists():
        try:
            dst.lookupByName(name)
        except libvirt.libvirtError:
            return [_c("nom_libre", OK, f"Aucune VM '{name}' sur la destination")]
        return [_c("nom_libre", BLOCKING, f"Une VM '{name}' existe déjà sur la destination")]

    def machine():
        os_type = xml.find("os/type")
        machine = os_type.get("machine") if os_type is not None else None
        if not machine:
            return [_c("machine", OK, "Type de machine non figé")]
        arch = (os_type.get("arch") or _host_arch(_caps(src)))
        supported = _machine_types(_caps(dst), arch)
        if machine in supported:
            return [_c("machine", OK, f"Type de machine {machine} supporté par la destination")]
        return [_c("machine", BLOCKING,
                   f"Type de machine {machine} inconnu de la destination (QEMU trop ancien)",
                   action="mettre à jour QEMU sur la destination, ou recréer la VM avec un type de machine générique (alias 'pc'/'q35')")]

    def cpu_vm():
        cpu = xml.find("cpu")
        if cpu is None:
            return [_c("cpu_vm", OK, "CPU de la VM non spécifique")]
        if cpu.get("mode") == "host-passthrough":
            return [_c("cpu_vm", WARNING, "CPU en host-passthrough : la migration exige des CPU physiques identiques",
                       action="préférer host-model ou un modèle générique")]
        return [_cpu_result("cpu_vm", dst, ET.tostring(cpu, encoding="unicode"), "CPU de la VM")]

    def firmware():
        os_el = xml.find("os")
        loader = os_el.find("loader") if os_el is not None else None
        uses_efi = loader is not None or (os_el is not None and os_el.get("firmware") == "efi")
        if not uses_efi:
            return [_c("firmware", OK, "Démarrage BIOS (aucun firmware UEFI requis)")]
        arch = (os_el.find("type").get("arch") if os_el.find("type") is not None else None) or _host_arch(_caps(src))
        dc = ET.fromstring(dst.getDomainCapabilities(None, arch, None, "kvm", 0))
        if dc.find("os/loader") is None or dc.find("os/loader").get("supported") != "yes":
            return [_c("firmware", BLOCKING, "La destination ne fournit aucun firmware UEFI (OVMF)",
                       action="apt install ovmf sur la destination")]
        return [_c("firmware", OK, "Firmware UEFI disponible sur la destination")]

    def networks():
        out = []
        for iface in xml.findall("devices/interface"):
            src_el = iface.find("source")
            if src_el is None:
                continue
            if iface.get("type") == "network":
                net = src_el.get("network")
                try:
                    n = dst.networkLookupByName(net)
                except libvirt.libvirtError:
                    out.append(_c(f"reseau:{net}", BLOCKING, f"Réseau '{net}' absent de la destination",
                                  action=f"créer un réseau '{net}' sur la destination"))
                    continue
                out.append(_c(f"reseau:{net}", OK,
                              f"Réseau '{net}' présent sur la destination" + ("" if n.isActive() else " (inactif : démarré automatiquement)")))
            elif iface.get("type") == "bridge":
                out.append(_c(f"pont:{src_el.get('bridge')}", WARNING,
                              f"Pont '{src_el.get('bridge')}' : existence sur la destination non vérifiable à distance",
                              action="vérifier que ce pont existe sur la destination"))
        return out or [_c("reseau", OK, "Aucune interface réseau à vérifier")]

    def disks():
        out = []
        shared = _netfs_pools(src)
        dst_shared = _netfs_pools(dst)
        to_copy_bytes = 0
        for disk in xml.findall("devices/disk"):
            if disk.get("device") != "disk":
                continue
            target = disk.find("target").get("dev")
            if disk.get("type") == "block":
                out.append(_c(f"disque:{target}", BLOCKING,
                              f"Disque {target} sur périphérique bloc (zvol ZFS) : migration à chaud non prise en charge par Hyperlite",
                              action="arrêter la VM et utiliser l'export/import, ou un stockage partagé fichier"))
                continue
            path = disk.find("source").get("file") if disk.find("source") is not None else None
            fmt = disk.find("driver").get("type") if disk.find("driver") is not None else None
            if fmt not in (None, "qcow2", "raw"):
                out.append(_c(f"format:{target}", WARNING, f"Format de disque {fmt} ({target}) : migration non testée"))
            is_shared = any(path and path.startswith(t) and key in dst_shared for key, t in shared.items())
            if is_shared:
                out.append(_c(f"disque:{target}", OK, f"Disque {target} sur stockage partagé (aucune copie)"))
            else:
                try:
                    size = domain.blockInfo(target, 0)[0]
                except libvirt.libvirtError:
                    size = 0
                to_copy_bytes += size
                out.append(_c(f"disque:{target}", OK, f"Disque {target} copié via le réseau ({size / 1024 ** 3:.1f} Go)"))
        if to_copy_bytes:
            pool = dst.storagePoolLookupByName("default")
            avail = pool.info()[3]
            if avail < to_copy_bytes:
                out.append(_c("espace_disque", BLOCKING,
                              f"Espace insuffisant sur la destination : {avail / 1024 ** 3:.1f} Go libres, {to_copy_bytes / 1024 ** 3:.1f} Go à copier",
                              action="libérer de la place ou utiliser un stockage partagé"))
            else:
                out.append(_c("espace_disque", OK, f"Espace suffisant sur la destination ({avail / 1024 ** 3:.1f} Go libres)"))
        return out or [_c("disques", OK, "Aucun disque à vérifier")]

    def memory():
        need_kib = domain.info()[2]
        st = dst.getMemoryStats(libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS, 0)
        avail_kib = st.get("free", 0) + st.get("cached", 0) + st.get("buffers", 0)
        if avail_kib < need_kib:
            return [_c("memoire", BLOCKING, f"Mémoire insuffisante sur la destination : {avail_kib // 1024} Mo disponibles, VM = {need_kib // 1024} Mo")]
        if avail_kib < need_kib * 1.2:
            return [_c("memoire", WARNING, f"Peu de marge mémoire sur la destination : {avail_kib // 1024} Mo disponibles pour {need_kib // 1024} Mo")]
        return [_c("memoire", OK, f"Mémoire suffisante sur la destination ({avail_kib // 1024} Mo disponibles)")]

    def hostdev():
        if xml.findall("devices/hostdev"):
            return [_c("peripheriques", BLOCKING, "Périphérique(s) passthrough (hostdev) attaché(s) : migration impossible",
                       action="détacher les périphériques passthrough")]
        return [_c("peripheriques", OK, "Aucun périphérique passthrough")]

    for id_, fn in (("etat_vm", state), ("nom_libre", exists), ("machine", machine), ("cpu_vm", cpu_vm),
                    ("firmware", firmware), ("reseaux", networks), ("disques", disks),
                    ("memoire", memory), ("peripheriques", hostdev)):
        checks += _safe(id_, fn)
    return checks


def report(checks):
    return {"resume": summarize(checks), "controles": checks}
