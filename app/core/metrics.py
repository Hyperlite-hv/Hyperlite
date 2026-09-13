"""Collecte continue de metriques (chantier 10 de la roadmap vSphere/vCenter,
2026-09-13) -- CPU/RAM/disque/reseau par VM et pour l'hote, echantillonnes en
arriere-plan a intervalle regulier et persistes en base, au lieu d'etre
recalcules a la demande (ce que faisait deja GET /vms/{name}/metrics -- ce
mecanisme-la reste tel quel pour le "temps reel instantane", il n'est pas
remplace ; celui-ci ajoute l'HISTORIQUE qui n'existait pas).

Simplification assumee par rapport aux 4 niveaux de stats de vCenter : deux
paliers seulement.
  - "raw"    : un echantillon toutes les COLLECT_INTERVAL_S secondes, garde
               RAW_RETENTION_H heures.
  - "hourly" : moyenne des echantillons raw de l'heure ecoulee, calculee une
               fois par heure avant que les raw correspondants ne soient
               purges, gardee HOURLY_RETENTION_DAYS jours.
Une vue "1h" lit le palier raw ; "24h/semaine/mois" lisent le palier hourly.
Pas de palier "journalier" en plus (vCenter en a un 3e/4e) -- juge suffisant
pour la taille de ce projet, a affiner si le volume de VM grandit beaucoup.

Alerting : seuils fixes (pas encore configurables par l'UI) verifies a
chaque tick ; un franchissement de seuil est journalise via log_action (donc
visible et filtrable dans le Journal existant, voir chantier 3) -- pas de
sous-systeme d'alarme separe avec etats acquittes/actifs comme vCenter, sur
le meme principe de ne pas sur-ingenierer pour la taille du projet.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

import libvirt

from app.core.database import get_conn
from app.core.libvirt_utils import open_conn

COLLECT_INTERVAL_S = 15
RAW_RETENTION_H = 2
HOURLY_RETENTION_DAYS = 60

ALERT_THRESHOLDS = {"cpu_pct": 90, "mem_pct": 90, "disk_pct": 90}

_last_counters = {}  # (cible, dev) -> (timestamp, valeur brute) pour calculer des debits
_alert_state = {}  # (cible, metrique) -> bool (deja en alerte ou pas), evite de spammer le journal a chaque tick
_stop_event = threading.Event()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _rate(key, now, raw_value):
    """Debit (unite/s) depuis la derniere lecture du meme compteur cumulatif.
    None au tout premier tick (pas encore de point de reference)."""
    prev = _last_counters.get(key)
    _last_counters[key] = (now, raw_value)
    if prev is None:
        return None
    prev_t, prev_v = prev
    dt = now - prev_t
    if dt <= 0 or raw_value < prev_v:  # compteur remis a zero (VM redemarree) -- pas de valeur negative
        return None
    return (raw_value - prev_v) / dt


def _host_cpu_pct():
    """% d'utilisation CPU hote depuis /proc/stat (delta de compteurs cumulatifs,
    memes champs que `top`/`vmstat`)."""
    try:
        with open("/proc/stat") as f:
            fields = [int(x) for x in f.readline().split()[1:]]
    except (OSError, ValueError):
        return None
    idle_all = fields[3] + fields[4]  # idle + iowait
    total = sum(fields)
    now = time.time()
    prev = _last_counters.get(("host", "cpu_ticks"))
    _last_counters[("host", "cpu_ticks")] = (now, (total, idle_all))
    if prev is None:
        return None
    _, (prev_total, prev_idle) = prev
    dt_total = total - prev_total
    if dt_total <= 0:
        return None
    return round((1 - (idle_all - prev_idle) / dt_total) * 100, 1)


def _host_mem_mb():
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = int(v.strip().split()[0])  # kB
        total = info.get("MemTotal", 0) / 1024
        available = info.get("MemAvailable", 0) / 1024
        return round(total - available, 1), round(total, 1)
    except (OSError, ValueError):
        return None, None


def _sample_vm(domain, name, now):
    if not domain.isActive():
        return None
    try:
        info = domain.info()
        nvcpu = info[3] or 1
        cpu_time = domain.getCPUStats(True)[0]["cpu_time"]
        cpu_rate = _rate((name, "cpu_time"), now, cpu_time)
        cpu_pct = round(min(100.0, (cpu_rate / 1e9) * 100 / nvcpu), 1) if cpu_rate is not None else None

        mem_stats = domain.memoryStats()
        mem_used_mb = round((mem_stats.get("actual", info[2]) - mem_stats.get("unused", 0)) / 1024, 1) if "unused" in mem_stats else None
        mem_total_mb = round(info[1] / 1024, 1)

        import xml.etree.ElementTree as ET
        root = ET.fromstring(domain.XMLDesc(0))
        read_bps = write_bps = rx_bps = tx_bps = 0.0
        for disk in root.findall(".//devices/disk"):
            if disk.get("device") != "disk":
                continue
            target = disk.find("target")
            if target is None or not target.get("dev"):
                continue
            try:
                rd_req, rd_bytes, wr_req, wr_bytes, err = domain.blockStats(target.get("dev"))
                r = _rate((name, f"rd:{target.get('dev')}"), now, rd_bytes)
                w = _rate((name, f"wr:{target.get('dev')}"), now, wr_bytes)
                read_bps += r or 0
                write_bps += w or 0
            except libvirt.libvirtError:
                pass
        for iface in root.findall(".//devices/interface"):
            target = iface.find("target")
            if target is None or not target.get("dev"):
                continue
            try:
                stats = domain.interfaceStats(target.get("dev"))
                rx, tx = stats[0], stats[4]
                r = _rate((name, f"rx:{target.get('dev')}"), now, rx)
                t = _rate((name, f"tx:{target.get('dev')}"), now, tx)
                rx_bps += r or 0
                tx_bps += t or 0
            except libvirt.libvirtError:
                pass

        return {
            "cpu_pct": cpu_pct, "mem_used_mb": mem_used_mb, "mem_total_mb": mem_total_mb,
            "disk_read_bps": round(read_bps, 1), "disk_write_bps": round(write_bps, 1),
            "net_rx_bps": round(rx_bps, 1), "net_tx_bps": round(tx_bps, 1),
        }
    except libvirt.libvirtError:
        return None


def _check_alert(cible, metric, value, threshold):
    key = (cible, metric)
    breached = value is not None and value >= threshold
    was_breached = _alert_state.get(key, False)
    _alert_state[key] = breached
    if breached and not was_breached:
        from app.core.audit import log_action
        log_action("system", "alert_seuil_depasse", cible, "echec", f"{metric} = {value}% (seuil {threshold}%)")


def _collect_tick():
    now = time.time()
    ts = _now_iso()
    rows = []

    host_cpu = _host_cpu_pct()
    host_mem_used, host_mem_total = _host_mem_mb()
    rows.append(("host", "host", host_cpu, host_mem_used, host_mem_total, None, None, None, None))
    if host_cpu is not None:
        _check_alert("host", "cpu_pct", host_cpu, ALERT_THRESHOLDS["cpu_pct"])
    if host_mem_used and host_mem_total:
        _check_alert("host", "mem_pct", round(host_mem_used / host_mem_total * 100, 1), ALERT_THRESHOLDS["mem_pct"])

    conn = open_conn()
    try:
        for domain in conn.listAllDomains():
            name = domain.name()
            s = _sample_vm(domain, name, now)
            if s is None:
                continue
            rows.append(("vm", name, s["cpu_pct"], s["mem_used_mb"], s["mem_total_mb"],
                         s["disk_read_bps"], s["disk_write_bps"], s["net_rx_bps"], s["net_tx_bps"]))
            if s["cpu_pct"] is not None:
                _check_alert(name, "cpu_pct", s["cpu_pct"], ALERT_THRESHOLDS["cpu_pct"])
            if s["mem_used_mb"] and s["mem_total_mb"]:
                _check_alert(name, "mem_pct", round(s["mem_used_mb"] / s["mem_total_mb"] * 100, 1), ALERT_THRESHOLDS["mem_pct"])
    finally:
        conn.close()

    with get_conn() as db:
        db.executemany(
            "INSERT INTO metrics_samples (ts, tier, scope, cible, cpu_pct, mem_used_mb, mem_total_mb, "
            "disk_read_bps, disk_write_bps, net_rx_bps, net_tx_bps) VALUES (?, 'raw', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(ts, scope, cible, *vals) for scope, cible, *vals in rows],
        )
        db.commit()


def _rollup_and_prune():
    """Une fois par heure : condense les echantillons raw de l'heure ecoulee
    en une moyenne par cible (palier 'hourly'), puis purge le vieux -- raw
    au-dela de RAW_RETENTION_H, hourly au-dela de HOURLY_RETENTION_DAYS."""
    now = datetime.now(timezone.utc)
    hour_ago = (now - timedelta(hours=1)).isoformat()
    raw_cutoff = (now - timedelta(hours=RAW_RETENTION_H)).isoformat()
    hourly_cutoff = (now - timedelta(days=HOURLY_RETENTION_DAYS)).isoformat()

    with get_conn() as db:
        cibles = db.execute("SELECT DISTINCT cible, scope FROM metrics_samples WHERE tier='raw' AND ts >= ?", (hour_ago,)).fetchall()
        for row in cibles:
            avg = db.execute(
                "SELECT AVG(cpu_pct), AVG(mem_used_mb), AVG(mem_total_mb), AVG(disk_read_bps), "
                "AVG(disk_write_bps), AVG(net_rx_bps), AVG(net_tx_bps) "
                "FROM metrics_samples WHERE tier='raw' AND cible=? AND ts >= ?",
                (row["cible"], hour_ago),
            ).fetchone()
            db.execute(
                "INSERT INTO metrics_samples (ts, tier, scope, cible, cpu_pct, mem_used_mb, mem_total_mb, "
                "disk_read_bps, disk_write_bps, net_rx_bps, net_tx_bps) VALUES (?, 'hourly', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now.isoformat(), row["scope"], row["cible"], *avg),
            )
        db.execute("DELETE FROM metrics_samples WHERE tier='raw' AND ts < ?", (raw_cutoff,))
        db.execute("DELETE FROM metrics_samples WHERE tier='hourly' AND ts < ?", (hourly_cutoff,))
        db.commit()


def _collector_loop():
    last_rollup = 0
    while not _stop_event.is_set():
        try:
            _collect_tick()
            if time.time() - last_rollup >= 3600:
                _rollup_and_prune()
                last_rollup = time.time()
        except Exception as e:  # ne jamais laisser le thread mourir sur un tick en echec
            print(f"[metrics] tick échoué : {e!r}", flush=True)
        _stop_event.wait(COLLECT_INTERVAL_S)


def start_metrics_collector():
    thread = threading.Thread(target=_collector_loop, daemon=True)
    thread.start()
    return thread
