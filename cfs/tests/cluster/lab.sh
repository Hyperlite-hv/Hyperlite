#!/bin/bash
# A three-node hyperlite-cfs cluster on one machine: one network namespace per node, joined by a bridge, each with its
# own Corosync and its own hyperlite-cfs in cluster mode. A "holder" process per node keeps the node's mount namespace
# (where its Corosync state directory, /run and /dev/shm are its own), so processes can be killed and started again in
# it. The addresses come from 192.0.2.0/24, a range reserved for documentation (RFC 5737).
#
#   lab.sh up BIN DIR    start the cluster; sockets are DIR/n1/cfs.sock ... DIR/n3/cfs.sock
#   lab.sh cut N         disconnect node N from the others
#   lab.sh heal N        reconnect it
#   lab.sh stop N        kill node N's hyperlite-cfs (SIGKILL: a crash of the daemon)
#   lab.sh crash N       kill node N's Corosync and hyperlite-cfs (SIGKILL: a crash of the node's cluster stack)
#   lab.sh start N       start what is not running on node N, Corosync first
#   lab.sh down [DIR]    stop everything and remove the namespaces (the logs stay in DIR)
#
# Needs root, iproute2, util-linux (unshare, nsenter) and corosync. Used by the tests in this directory.
set -euo pipefail

NODES=3
P=hlcfs
STATE=/tmp/${P}-nslab # BIN and DIR of the running lab, for the commands that take only a node

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

alive() { # alive PIDFILE: the process it names is running
    [ -s "$1" ] && kill -0 "$(cat "$1")" 2> /dev/null
}

in_node() { # in_node DIR N command: run a shell command in node N's network and mount namespaces
    nsenter -t "$(cat "$1/n$2/holder.pid")" -m -n sh -c "$3"
}

kill_wait() { # kill_wait PIDFILE: SIGKILL the process and wait until it is gone
    if alive "$1"; then
        local pid
        pid=$(cat "$1")
        kill -9 "$pid" 2> /dev/null || true
        while kill -0 "$pid" 2> /dev/null; do sleep 0.05; done
    fi
    rm -f "$1"
}

start_node() { # start_node BIN DIR N: Corosync if it is not running, then hyperlite-cfs if it is not
    local bin=$1 dir=$2 i=$3
    if ! alive "$dir/n$i/corosync.pid"; then
        in_node "$dir" "$i" "corosync -f >> '$dir/n$i/corosync.log' 2>&1 & echo \$! > '$dir/n$i/corosync.pid'"
        # hyperlite-cfs once Corosync answers on its local socket (cmap answers without the other nodes).
        in_node "$dir" "$i" "for _ in \$(seq 1 150); do corosync-cmapctl totem.cluster_name > /dev/null 2>&1 && break; sleep 0.2; done"
    fi
    if ! alive "$dir/n$i/cfs.pid"; then
        in_node "$dir" "$i" "'$bin' --cluster --db '$dir/n$i/config.db' --socket '$dir/n$i/cfs.sock' --socket-mode 0666 \
            >> '$dir/n$i/cfs.log' 2>&1 & echo \$! > '$dir/n$i/cfs.pid'"
    fi
}

up() {
    local bin dir=$2
    bin=$(realpath "$1")
    printf '%s\n%s\n' "$bin" "$dir" > $STATE
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
        rm -f "$dir/n$i/holder.pid"
        ip netns exec ${P}$i unshare -m sh -c "
            mount --bind '$dir/n$i/lib' /var/lib/corosync
            mount --bind '$dir/n$i/run' /run
            mount -t tmpfs tmpfs /dev/shm
            echo \$\$ > '$dir/n$i/holder.pid'
            exec sleep infinity
        " > /dev/null 2>&1 &
        for _ in $(seq 1 100); do [ -s "$dir/n$i/holder.pid" ] && break || sleep 0.05; done
        start_node "$bin" "$dir" "$i"
    done
    # Hand the cluster over only once the three daemons are members of one process group.
    for _ in $(seq 1 120); do
        local n
        n=$(in_node "$dir" 1 corosync-cpgtool 2> /dev/null |
            awk '/^hyperlite-cfs/ { g = 1; next } /^[^[:space:]]/ { g = 0 } g && NF { n++ } END { print n + 0 }') || n=0
        [ "$n" -eq $NODES ] && return 0
        sleep 1
    done
    echo "lab: the three daemons never formed one group" >&2
    return 1
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
    rm -f $STATE
}

lab_bin() { sed -n 1p $STATE; }
lab_dir() { sed -n 2p $STATE; }

case ${1:-} in
    up) up "$2" "$3" ;;
    down) down ;;
    cut) ip link set ${P}v$2 down ;;
    heal) ip link set ${P}v$2 up ;;
    stop) kill_wait "$(lab_dir)/n$2/cfs.pid" ;;
    crash)
        kill_wait "$(lab_dir)/n$2/corosync.pid"
        kill_wait "$(lab_dir)/n$2/cfs.pid"
        ;;
    start) start_node "$(lab_bin)" "$(lab_dir)" "$2" ;;
    *)
        echo "usage: $0 up BIN DIR | cut N | heal N | stop N | crash N | start N | down" >&2
        exit 2
        ;;
esac
