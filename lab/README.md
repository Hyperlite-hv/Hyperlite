# Test lab

`vm.sh` builds a three-node lab of real Debian 13 VMs on one KVM host: each VM runs its own kernel, Corosync and
`hyperlite-cfs` built from the checkout, and has a watchdog device for the HA tests. The VMs sit on a NAT network in
`192.0.2.0/24`, a range reserved for documentation.

It is no longer run by the CI: run it by hand, on any Debian or Ubuntu host with libvirt and KVM (not a node in
production: it starts several VMs):

```bash
sudo apt-get install libvirt-daemon-system qemu-system-x86 qemu-utils virtinst dnsmasq-base cloud-image-utils
sudo env HYPERLITE_CFS_CLUSTER=1 HYPERLITE_CFS_LAB="$PWD/lab/vm.sh" venv/bin/python -m pytest -q -s cfs/tests/cluster
```

The tests (`cfs/tests/cluster/test_cluster.py`) drive either this lab or the network namespaces of
`cfs/tests/cluster/lab.sh`, which the CI's `cfs` job uses on every pull request: both scripts take the same commands
(`up`, `cut N`, `heal N`, `stop N`, `crash N`, `start N`, `down`).

What the lab cannot show: real hardware, the latency of a real link between sites, and a real NAS. Those are checked
on real machines with a written procedure before production.

## Hyperlite on the lab

`vm.sh hyperlite DEB DIR` installs the Hyperlite package `DEB` on the same three VMs instead (its dependencies from
Debian), and `lab/tests` makes them one cluster through the API, as an administrator does. Then it checks what only
real members show: a request naming another member (`?node=`) is done by that member, an ISO upload is relayed to it,
a VM made on one member is migrated to another (when the host lets the VMs run VMs of their own), and the Terraform
provider manages every member's objects through one node. It builds the package as the publication does and runs it;
on a machine of your own:

```bash
bash installer/build-deb.sh   # after installer/build-runtime.sh, see installer/
sudo env HYPERLITE_LAB_DEB=$PWD/installer/hyperlite_X_amd64.deb venv/bin/python -m pytest -q -s lab/tests
```

`HYPERLITE_LAB_PROVIDER` (the directory of a built `terraform-provider-hyperlite`, with `terraform` on the PATH) adds
the provider's test; `HYPERLITE_LAB_DIR` keeps each node's logs (`nN/hyperlite.log`); `HYPERLITE_LAB_MEMORY` sets each
node's memory in MB (3072 by default; 2048 is enough, the VM of the migration test takes 256). The VM test runs only
where the nodes get nested virtualization (`/sys/module/kvm_*/parameters/nested` is `Y` or `1`): skipped on GitHub's
runners.

`HYPERLITE_LAB_API_USER=www-data` runs the API as that account on every node (`lab/tests/conftest.py`, the probe of
`docs/design/privilege-separation.md`'s step 4e): a drop-in on `hyperlite.service` right after the package is installed,
or once the cluster is formed as root with `HYPERLITE_LAB_API_AFTER=cluster`. `HYPERLITE_LAB_API_DROPIN_FILE` gives the
whole drop-in instead, and `HYPERLITE_LAB_API_PATCH_FILE` a script run as root on each node first (a lab's workaround
for what a pull request has not changed yet). Before the lab is taken down, every refusal in each node's journal goes to
`nN/api-user.txt` in `HYPERLITE_LAB_DIR`.
