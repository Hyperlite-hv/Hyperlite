# Test lab

`vm.sh` builds a three-node lab of real Debian 13 VMs on one KVM host: each VM runs its own kernel, Corosync and
`hyperlite-cfs` built from the checkout, and has a watchdog device for the HA tests. The VMs sit on a NAT network in
`192.0.2.0/24`, a range reserved for documentation.

It runs on a GitHub runner (`.github/workflows/lab.yml`: on changes to `cfs/` or `lab/`, every night, and on demand),
so the lab needs no access to anyone's machines. It runs the same way on any Debian or Ubuntu host with libvirt and
KVM:

```bash
sudo apt-get install libvirt-daemon-system qemu-system-x86 qemu-utils virtinst cloud-image-utils
sudo env HYPERLITE_CFS_CLUSTER=1 HYPERLITE_CFS_LAB="$PWD/lab/vm.sh" venv/bin/python -m pytest -q -s cfs/tests/cluster
```

The tests (`cfs/tests/cluster/test_cluster.py`) drive either this lab or the network namespaces of
`cfs/tests/cluster/lab.sh`, which the CI's `cfs` job uses on every pull request: both scripts take the same commands
(`up`, `cut N`, `heal N`, `down`).

What the lab cannot show: real hardware, the latency of a real link between sites, and a real NAS. Those are checked
on real machines with a written procedure before production.
