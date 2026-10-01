#!/bin/bash
# A three-node lab of real Debian 13 VMs on one KVM host (a GitHub runner, or any Debian/Ubuntu machine with libvirt):
# each VM has its own kernel, its own Corosync and its own hyperlite-cfs built from this checkout, and a watchdog
# device for the HA tests to come. The VMs sit on a NAT network in 192.0.2.0/24, a range reserved for documentation
# (RFC 5737), so nothing here names a real address.
#
#   vm.sh up BIN DIR   create the VMs, build hyperlite-cfs on each, start Corosync and the daemon, and forward each
#                      daemon's socket to DIR/n1/cfs.sock ... DIR/n3/cfs.sock (BIN is ignored: the VMs build their own)
#   vm.sh cut N        cut node N from the others (Corosync's traffic dropped by nftables; SSH stays up)
#   vm.sh heal N       let it through again
#   vm.sh down [DIR]   keep each node's logs in DIR/nN/cfs.log, then destroy everything
#
# Needs root, libvirt with KVM, dnsmasq, virt-install, cloud-image-utils (cloud-localds), openssh-client and Internet access
# for the Debian image and packages. Same interface as cfs/tests/cluster/lab.sh, so the same tests drive both.
set -euo pipefail

NODES=3
P=hlcfs
NET=${P}lab
REPO=$(cd "$(dirname "$0")/.." && pwd)
# The "generic" image, not "genericcloud": the latter's kernel carries only the drivers of some clouds, and a VM whose
# disk or network card it lacks boots blind.
IMAGE_URL=${HYPERLITE_LAB_IMAGE:-https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2}
CACHE=${HYPERLITE_LAB_CACHE:-/var/cache/hyperlite-lab}
POOL=/var/lib/libvirt/images

ip_of() { echo "192.0.2.$1"; }
mac_of() { printf '52:54:00:c0:02:%02x' "$1"; }
key_of() { echo "$1/id_ed25519"; }

on() { # on DIR N command...: run a command on node N as root
    local dir=$1 n=$2
    shift 2
    ssh -i "$(key_of "$dir")" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
        -o ConnectTimeout=5 "debian@$(ip_of "$n")" "sudo $(printf '%q ' "$@")"
}

corosync_conf() {
    cat <<'EOF'
totem {
  version: 2
  cluster_name: hlcfs-vmlab
  transport: knet
  crypto_cipher: none
  crypto_hash: none
}
nodelist {
  node {
    ring0_addr: 192.0.2.1
    nodeid: 1
  }
  node {
    ring0_addr: 192.0.2.2
    nodeid: 2
  }
  node {
    ring0_addr: 192.0.2.3
    nodeid: 3
  }
}
quorum {
  provider: corosync_votequorum
}
logging {
  to_syslog: yes
}
EOF
}

network() {
    local hosts=""
    for i in $(seq 1 $NODES); do
        hosts+="<host mac='$(mac_of "$i")' name='${P}$i' ip='$(ip_of "$i")'/>"
    done
    cat > /tmp/$NET.xml <<EOF
<network><name>$NET</name><forward mode='nat'/><bridge name='${NET}0'/>
<ip address='192.0.2.254' netmask='255.255.255.0'><dhcp><range start='192.0.2.100' end='192.0.2.200'/>$hosts</dhcp></ip>
</network>
EOF
    virsh net-define /tmp/$NET.xml > /dev/null
    virsh net-start $NET > /dev/null
}

vm() { # vm DIR N: a VM from the cached image, with cloud-init giving it our key
    local dir=$1 i=$2
    qemu-img create -q -f qcow2 -F qcow2 -b "$POOL/${P}-base.qcow2" "$POOL/${P}$i.qcow2" 10G
    cat > "$dir/n$i/user-data" <<EOF
#cloud-config
hostname: ${P}$i
users:
  - name: debian
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
      - $(cat "$(key_of "$dir").pub")
EOF
    echo "instance-id: ${P}$i" > "$dir/n$i/meta-data"
    cloud-localds "$POOL/${P}$i-seed.iso" "$dir/n$i/user-data" "$dir/n$i/meta-data"
    # virtio everywhere, said explicitly: without an OS virt-install knows, it falls back to emulated SATA and NICs. The
    # cloud-init seed is a plain virtio disk too (cloud-init finds it by its "cidata" label).
    virt-install --name ${P}$i --memory 1536 --vcpus 2 --import \
        --disk "path=$POOL/${P}$i.qcow2,bus=virtio" \
        --disk "path=$POOL/${P}$i-seed.iso,device=disk,bus=virtio,format=raw,readonly=on" \
        --network network=$NET,mac="$(mac_of "$i")",model=virtio --watchdog i6300esb,action=reset \
        --osinfo detect=on,require=off --graphics none --noautoconsole \
        --serial file,path=/var/log/libvirt/qemu/${P}$i-console.log > /dev/null
}

provision() { # provision DIR N: build hyperlite-cfs from this checkout and start it with Corosync
    local dir=$1 i=$2
    local reached=0
    for _ in $(seq 1 90); do
        if on "$dir" "$i" true 2> /dev/null; then
            reached=1
            break
        fi
        sleep 2
    done
    if [ $reached -eq 0 ]; then
        echo "node $i never answered on SSH" >&2
        return 1
    fi
    echo "node $i: up, provisioning"
    on "$dir" "$i" cloud-init status --wait || true
    on "$dir" "$i" env DEBIAN_FRONTEND=noninteractive apt-get update -q
    on "$dir" "$i" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
        corosync libcpg-dev libquorum-dev meson ninja-build gcc pkg-config libsqlite3-dev libssl-dev nftables
    tar -C "$REPO" -cz cfs | on "$dir" "$i" sh -c 'rm -rf /opt/cfs && mkdir -p /opt && tar -C /opt -xz'
    # Warnings stay warnings here: a newer compiler than the CI's must not stop the lab (the CI keeps -Werror).
    on "$dir" "$i" sh -c 'meson setup -Dwerror=false /opt/cfs/build /opt/cfs && ninja -C /opt/cfs/build'
    corosync_conf | on "$dir" "$i" tee /etc/corosync/corosync.conf > /dev/null
    on "$dir" "$i" systemctl restart corosync
    on "$dir" "$i" mkdir -p /var/lib/hyperlite-cfs /run/hyperlite-cfs
    # The socket is opened to every local user, since the test reaches it through an SSH forward as `debian`.
    on "$dir" "$i" systemd-run --unit hyperlite-cfs /opt/cfs/build/hyperlite-cfs --cluster \
        --db /var/lib/hyperlite-cfs/config.db --socket /run/hyperlite-cfs/socket --socket-mode 0666 > /dev/null
    for _ in $(seq 1 60); do on "$dir" "$i" test -S /run/hyperlite-cfs/socket && break || sleep 1; done
    ssh -i "$(key_of "$dir")" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
        -o ExitOnForwardFailure=yes -o StreamLocalBindUnlink=yes -o ServerAliveInterval=5 -f -N \
        -L "$dir/n$i/cfs.sock:/run/hyperlite-cfs/socket" "debian@$(ip_of "$i")"
    chmod 0666 "$dir/n$i/cfs.sock"
}

up() {
    local dir=$1
    mkdir -p "$CACHE" "$POOL"
    [ -s "$CACHE/debian.qcow2" ] || curl -fsSL -o "$CACHE/debian.qcow2" "$IMAGE_URL"
    # The base image sits in libvirt's own directory, where QEMU and AppArmor allow reading a backing file.
    cp "$CACHE/debian.qcow2" "$POOL/${P}-base.qcow2"
    ssh-keygen -q -t ed25519 -N '' -f "$(key_of "$dir")"
    network
    for i in $(seq 1 $NODES); do
        mkdir -p "$dir/n$i"
        vm "$dir" "$i"
    done
    local pids=()
    for i in $(seq 1 $NODES); do
        provision "$dir" "$i" > "$dir/n$i/provision.log" 2>&1 &
        pids+=($!)
    done
    local failed=0
    for p in "${pids[@]}"; do wait "$p" || failed=1; done
    if [ $failed -ne 0 ]; then
        tail -n 30 "$dir"/n*/provision.log >&2
        # What the VMs and the network say, to tell a VM that did not boot from one without an address.
        virsh list --all >&2 || true
        virsh net-dhcp-leases $NET >&2 || true
        for i in $(seq 1 $NODES); do
            echo "--- console of node $i" >&2
            tail -n 40 /var/log/libvirt/qemu/${P}$i-console.log >&2 2> /dev/null || true
        done
        return 1
    fi
}

down() {
    local dir=${1:-}
    for i in $(seq 1 $NODES); do
        if [ -n "$dir" ] && [ -f "$(key_of "$dir")" ]; then
            mkdir -p "$dir/n$i"
            on "$dir" "$i" journalctl -u corosync -u hyperlite-cfs --no-pager > "$dir/n$i/cfs.log" 2>&1 || true
        fi
        virsh destroy ${P}$i > /dev/null 2>&1 || true
        virsh undefine ${P}$i > /dev/null 2>&1 || true
        rm -f "$POOL/${P}$i.qcow2" "$POOL/${P}$i-seed.iso"
    done
    rm -f "$POOL/${P}-base.qcow2"
    pkill -f "ssh .*-L .*/n[0-9]/cfs.sock" 2> /dev/null || true
    virsh net-destroy $NET > /dev/null 2>&1 || true
    virsh net-undefine $NET > /dev/null 2>&1 || true
}

# Corosync's knet traffic is UDP on port 5405; dropping it both ways splits the node off, SSH keeps working.
cut() {
    on "$1" "$2" nft -f - <<'EOF'
table inet hlcfs_cut {
  chain input { type filter hook input priority 0; udp dport 5405 drop; }
  chain output { type filter hook output priority 0; udp dport 5405 drop; }
}
EOF
}

heal() {
    on "$1" "$2" nft delete table inet hlcfs_cut
}

# cut and heal need the directory of the SSH key: the tests keep the last one used in this file.
STATE=/tmp/${P}-vmlab-dir
case ${1:-} in
    up)
        echo "$3" > $STATE
        up "$3"
        ;;
    down) down "${2:-$(cat $STATE 2> /dev/null || true)}" ;;
    cut) cut "$(cat $STATE)" "$2" ;;
    heal) heal "$(cat $STATE)" "$2" ;;
    *)
        echo "usage: $0 up BIN DIR | cut N | heal N | down [DIR]" >&2
        exit 2
        ;;
esac
