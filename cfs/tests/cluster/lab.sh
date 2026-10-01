#!/bin/bash
# A three-node hyperlite-cfs cluster on one machine: one network namespace per node, joined by a bridge, each with its
# own Corosync (in its own mount namespace, so their state directories do not collide) and its own hyperlite-cfs in
# cluster mode. The addresses come from 192.0.2.0/24, a range reserved for documentation (RFC 5737).
#
#   lab.sh up BIN DIR    start the cluster; sockets are DIR/n1/cfs.sock ... DIR/n3/cfs.sock
#   lab.sh cut N         disconnect node N from the others
#   lab.sh heal N        reconnect it
#   lab.sh down [DIR]    stop everything and remove the namespaces (the logs stay in DIR)
#
# Needs root, iproute2, util-linux (unshare) and corosync. Used by test_cluster.py.
set -euo pipefail

NODES=3
P=hlcfs

conf() {
    cat <<'EOF'
totem {
  version: 2
  cluster_name: hlcfs-test
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
  to_stderr: yes
  to_syslog: no
}
EOF
}

up() {
    local bin dir=$2
    bin=$(realpath "$1")
    ip link add ${P}br type bridge
    ip link set ${P}br up
    for i in $(seq 1 $NODES); do
        ip netns add ${P}$i
        ip link add ${P}v$i type veth peer name ${P}e$i
        ip link set ${P}v$i master ${P}br up
        ip link set ${P}e$i netns ${P}$i
        ip -n ${P}$i addr add 192.0.2.$i/24 dev ${P}e$i
        ip -n ${P}$i link set ${P}e$i up
        ip -n ${P}$i link set lo up
        mkdir -p /etc/netns/${P}$i/corosync "$dir/n$i/lib" "$dir/n$i/run"
        conf > /etc/netns/${P}$i/corosync/corosync.conf
        # Corosync first; hyperlite-cfs once Corosync answers on its local socket (cmap answers without the other
        # nodes, unlike the link status). Both stay in the node's namespaces.
        ip netns exec ${P}$i unshare -m sh -c "
            mount --bind '$dir/n$i/lib' /var/lib/corosync
            mount --bind '$dir/n$i/run' /run
            corosync -f > '$dir/n$i/corosync.log' 2>&1 &
            for _ in \$(seq 1 150); do corosync-cmapctl totem.cluster_name > /dev/null 2>&1 && break; sleep 0.2; done
            exec '$bin' --cluster --db '$dir/n$i/config.db' --socket '$dir/n$i/cfs.sock' --socket-mode 0666
        " > "$dir/n$i/cfs.log" 2>&1 &
    done
}

down() {
    for i in $(seq 1 $NODES); do
        if ip netns list | grep -qw ${P}$i; then
            ip netns pids ${P}$i | xargs -r kill 2> /dev/null || true
            sleep 0.5
            ip netns pids ${P}$i | xargs -r kill -9 2> /dev/null || true
            ip netns del ${P}$i
        fi
        rm -rf /etc/netns/${P}$i
    done
    ip link del ${P}br 2> /dev/null || true
}

case ${1:-} in
    up) up "$2" "$3" ;;
    down) down ;;
    cut) ip link set ${P}v$2 down ;;
    heal) ip link set ${P}v$2 up ;;
    *)
        echo "usage: $0 up BIN DIR | cut N | heal N | down" >&2
        exit 2
        ;;
esac
