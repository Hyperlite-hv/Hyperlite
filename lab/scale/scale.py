#!/usr/bin/env python3
"""Scale test on a real Hyperlite cluster (docs/design/scale-test.md, part 1), through the API only, as users do.

    HL_NODES="hlc1=https://127.0.0.1:8441,hlc2=https://127.0.0.1:8442,hlc3=https://127.0.0.1:8443" \\
    HL_PASSWORD_FILE=/path/to/admin-password  scale.py COMMAND [options]

Commands:
  populate --per-node N   upload a tiny ISO to each node, then create N VMs per node (scale-<node>-NNNN: 1 vCPU,
                          256 MB (the minimum), a 1 GB thin disk, booting the ISO, so nothing is copied)
  start --per-node K      start K of them per node (they stay in the firmware: nothing to boot)
  measure [--runs R]      latency of the list endpoints of every node, and of the VM list as the dashboard loads it
  load --users U --seconds S   U users refreshing the dashboard's VM list every 5 s; latencies and errors
  meta                    notes and tags written on every VM of the first node; time until every node has them
  cleanup                 delete every scale-* VM and the ISO

Only the standard library; certificates are not checked (lab nodes have self-signed ones). Never run against
production: it creates and deletes VMs.
"""

import argparse
import concurrent.futures as cf
import json
import os
import ssl
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

CTX = ssl._create_unverified_context()  # noqa: S323 - lab nodes have self-signed certificates
PREFIX = "scale-"
ISO = "scale-empty.iso"


def nodes():
    raw = os.environ.get("HL_NODES") or sys.exit("HL_NODES is not set (name=url,name=url)")
    return dict(item.split("=", 1) for item in raw.split(","))


class Api:
    def __init__(self, url):
        self.url, self.token, self.lock = url, None, threading.Lock()

    def login(self):
        with open(os.environ["HL_PASSWORD_FILE"]) as f:
            password = f.read().strip()
        body = urllib.parse.urlencode({"username": "admin", "password": password}).encode()
        status, data = self._send("POST", "/auth/login", body, {"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            sys.exit(f"sign-in on {self.url} failed: {status} {data}")
        self.token = data["access_token"]

    def _send(self, method, path, body=None, headers=None, timeout=120):
        # The operator gives the nodes' https:// addresses (HL_NODES).
        req = urllib.request.Request(self.url + path, data=body, method=method, headers=headers or {})  # noqa: S310
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:  # noqa: S310
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw[:200]
        except OSError as e:
            return None, str(e)

    def call(self, method, path, payload=None, timeout=120):
        body = json.dumps(payload).encode() if payload is not None else None
        return self._send(method, path, body, {"Content-Type": "application/json"}, timeout)

    def timed(self, method, path, payload=None):
        t0 = time.perf_counter()
        status, data = self.call(method, path, payload)
        return time.perf_counter() - t0, status, data

    def upload(self, path, name, content):
        boundary = uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self._send("POST", path, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def apis():
    out = {name: Api(url) for name, url in nodes().items()}
    for api in out.values():
        api.login()
    return out


def tiny_iso():
    """An ISO 9660 image with one empty file: enough for libvirt to attach and for the VM to find nothing to boot."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "README"), "w") as f:
            f.write("Hyperlite scale test\n")
        out = os.path.join(tmp, "x.iso")
        subprocess.run(["genisoimage", "-quiet", "-o", out, "-V", "SCALE", tmp], check=True)
        with open(out, "rb") as f:
            return f.read()


def names(node, count):
    return [f"{PREFIX}{node}-{i:04d}" for i in range(count)]


def populate(args):
    iso = tiny_iso()
    for node, api in apis().items():
        status, _ = api.upload("/isos", ISO, iso)
        print(f"{node}: ISO {status}", flush=True)
        existing = {v.get("nom") for v in (api.call("GET", "/vms")[1] or [])}
        todo = [n for n in names(node, args.per_node) if n not in existing]
        t0, failures = time.perf_counter(), []

        def create(name, api=api, failures=failures):
            payload = {"name": name, "vcpu": 1, "memory_mb": 256, "disks": [{"size_gb": 1}], "iso": ISO}
            s, d = api.call("POST", "/vms", payload, timeout=300)
            if s not in (200, 201, 202):
                failures.append((name, s, str(d)[:160]))

        with cf.ThreadPoolExecutor(args.parallel) as pool:
            list(pool.map(create, todo))
        print(f"{node}: {len(todo)} created in {time.perf_counter() - t0:.0f} s, {len(failures)} failed "
              f"{failures[:3]}", flush=True)  # fmt: skip


def start(args):
    for node, api in apis().items():
        for name in names(node, args.per_node):
            s, d = api.call("POST", f"/vms/{name}/start")
            print(f"{node} start {name}: {s}" + ("" if s in (200, 202) else f" {str(d)[:120]}"), flush=True)


def _stats(samples):
    samples = sorted(samples)
    p = lambda q: samples[min(len(samples) - 1, int(q * len(samples)))]  # noqa: E731
    return {"n": len(samples), "min": samples[0], "p50": statistics.median(samples), "p95": p(0.95), "max": samples[-1]}


def ui_vm_list(api):
    """The VM list as the dashboard loads it (dashboard/src/api/client.js, fetchVMs): every member's VMs in one request
    (GET /vms?cluster=true, phase E) and the nodes, then the nodes registered over SSH only. A node without the cluster
    view answers a bare list: then every other node is asked, as the dashboard did before. Returns (seconds, VMs,
    errors)."""
    t0 = time.perf_counter()
    errors, total = [], 0
    with cf.ThreadPoolExecutor(2) as pool:
        listed, nodes_call = pool.submit(api.call, "GET", "/vms?cluster=true"), pool.submit(api.call, "GET", "/nodes")
        (s1, data), (s2, nodes_list) = listed.result(), nodes_call.result()
    if s1 != 200:
        errors.append(("/vms?cluster=true", s1))
    nodes_list = [n for n in (nodes_list or []) if isinstance(n, dict) and n.get("name")] if s2 == 200 else []
    if isinstance(data, dict):
        total += len(data.get("items") or [])
        others = [n["name"] for n in nodes_list if not n.get("membre")]
    else:
        total += len(data or [])
        others = [n["name"] for n in nodes_list]
    with cf.ThreadPoolExecutor(max(1, len(others))) as pool:
        for s, vms in pool.map(lambda n: api.call("GET", f"/vms?node={urllib.parse.quote(n)}"), others):
            if s != 200:
                errors.append(("/vms?node", s))
            total += len(vms or []) if s == 200 else 0
    return time.perf_counter() - t0, total, errors


ENDPOINTS = ["/vms", "/nodes", "/dashboard", "/tasks?limit=50", "/tasks?limit=50&cluster=true",
             "/audit?limit=50", "/audit?limit=50&cluster=true", "/cluster", "/storage", "/networks", "/meta?kind=vm"]  # fmt: skip


def measure(args):
    report = {}
    for node, api in apis().items():
        for path in ENDPOINTS:
            times, codes = [], set()
            for _ in range(args.runs):
                dt, s, _ = api.timed("GET", path)
                times.append(dt)
                codes.add(s)
            report[f"{node} {path}"] = {**_stats(times), "codes": sorted(map(str, codes))}
        runs = [ui_vm_list(api) for _ in range(args.runs)]
        report[f"{node} UI VM list"] = {**_stats([r[0] for r in runs]), "vms": runs[-1][1],
                                        "errors": sum(len(r[2]) for r in runs)}  # fmt: skip
    for key, s in report.items():
        extra = " ".join(f"{k}={v}" for k, v in s.items() if k not in ("n", "min", "p50", "p95", "max"))
        print(
            f"{key:44s} p50 {s['p50'] * 1000:7.0f} ms  p95 {s['p95'] * 1000:7.0f} ms  max {s['max'] * 1000:7.0f} ms  {extra}"
        )
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=1)


def load(args):
    pool = list(apis().values())
    stop, lock, times, errors = time.monotonic() + args.seconds, threading.Lock(), [], []

    def user(i):
        api = pool[i % len(pool)]
        while time.monotonic() < stop:
            dt, _, errs = ui_vm_list(api)
            with lock:
                times.append(dt)
                errors.extend(errs)
            time.sleep(max(0.0, 5 - dt))

    threads = [threading.Thread(target=user, args=(i,)) for i in range(args.users)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s = _stats(times)
    print(f"{args.users} users, {args.seconds} s: {s['n']} VM lists, p50 {s['p50'] * 1000:.0f} ms, "
          f"p95 {s['p95'] * 1000:.0f} ms, max {s['max'] * 1000:.0f} ms, {len(errors)} errors {errors[:5]}")  # fmt: skip


def meta(args):
    """Notes and a tag of this run on every scale VM of the first node, then the time until the other nodes list
    them all (GET /meta: every object's tags, replicated through hyperlite-cfs)."""
    all_apis = list(apis().items())
    (node, api), others = all_apis[0], all_apis[1:]
    vms = [v["nom"] for v in api.call("GET", "/vms")[1] if v["nom"].startswith(PREFIX)]
    tag = f"scale{time.strftime('%H%M%S')}"
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda n: api.call("PUT", f"/meta/vm/{n}", {"notes": tag, "tags": [tag]})[0], vms))
    print(f"{node}: {len(vms)} notes written in {time.perf_counter() - t0:.1f} s, codes {sorted(set(map(str, codes)))}",
          flush=True)  # fmt: skip
    for other, oapi in others:
        while True:
            s, data = oapi.call("GET", "/meta?kind=vm")
            seen = sum(1 for m in (data or []) if tag in m.get("tags", [])) if s == 200 else 0
            if seen >= len(vms) or time.perf_counter() - t0 > 300:
                break
            time.sleep(0.5)
        print(f"{other}: {seen}/{len(vms)} there {time.perf_counter() - t0:.1f} s after the first write", flush=True)


def cleanup(args):
    for node, api in apis().items():
        vms = [v["nom"] for v in (api.call("GET", "/vms")[1] or []) if v["nom"].startswith(PREFIX)]
        for name in vms:
            if any(
                v.get("nom") == name and v.get("etat") == "actif" for v in [api.call("GET", f"/vms/{name}")[1] or {}]
            ):
                api.call("POST", f"/vms/{name}/stop?force=true")
        with cf.ThreadPoolExecutor(args.parallel) as pool:
            codes = list(pool.map(lambda n, api=api: api.call("DELETE", f"/vms/{n}?confirm=true", timeout=300)[0], vms))
        api.call("DELETE", f"/isos/{ISO}")
        print(f"{node}: {len(vms)} deleted, codes {sorted(set(map(str, codes)))}", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("populate")
    s.add_argument("--per-node", type=int, required=True)
    s.add_argument("--parallel", type=int, default=4)
    s = sub.add_parser("start")
    s.add_argument("--per-node", type=int, required=True)
    s = sub.add_parser("measure")
    s.add_argument("--runs", type=int, default=20)
    s.add_argument("--json")
    s = sub.add_parser("load")
    s.add_argument("--users", type=int, default=20)
    s.add_argument("--seconds", type=int, default=300)
    sub.add_parser("meta")
    s = sub.add_parser("cleanup")
    s.add_argument("--parallel", type=int, default=4)
    args = p.parse_args()
    {"populate": populate, "start": start, "measure": measure, "load": load, "meta": meta, "cleanup": cleanup}[
        args.cmd
    ](args)


if __name__ == "__main__":
    main()
