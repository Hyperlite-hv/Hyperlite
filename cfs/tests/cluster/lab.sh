#!/bin/bash
# A hyperlite-cfs cluster on one machine: one network namespace per node, joined by a bridge, each with its own
# Corosync and its own hyperlite-cfs in cluster mode. A "holder" process per node keeps the node's mount namespace
# (where its Corosync state directory, /run and /dev/shm are its own), so processes can be killed and started again in
# it. The addresses come from 192.0.2.0/24, a range reserved for documentation (RFC 5737).
#
#   lab.sh up BIN DIR    start the cluster; sockets are DIR/n1/cfs.sock, DIR/n2/cfs.sock...
#   lab.sh cut N         disconnect node N from the others
#   lab.sh heal N        reconnect it
#   lab.sh stop N        kill node N's hyperlite-cfs (SIGKILL: a crash of the daemon)
#   lab.sh crash N       kill node N's Corosync and hyperlite-cfs (SIGKILL: a crash of the node's cluster stack)
#   lab.sh start N       start what is not running on node N, Corosync first
#   lab.sh qnetd stop|start   stop or start the QDevice arbiter (with HYPERLITE_LAB_QDEVICE=1)
#   lab.sh tool N ARGS...     run `hyperlite-cfs ARGS...` on node N (its exit status is the lab's)
#   lab.sh down [DIR]    stop everything and remove the namespaces (the logs stay in DIR)
#
# HYPERLITE_LAB_NODES sets the number of nodes (3 by default). HYPERLITE_LAB_QDEVICE=1 adds the arbiter of a two-node
# cluster as Proxmox recommends it: corosync-qnetd in a namespace of its own, corosync-qdevice next to each node's
# Corosync, and wait_for_all (design, section 5). The lab keeps both settings for its later commands.
#
# Needs root, iproute2, util-linux (unshare, nsenter) and corosync (corosync-qnetd and corosync-qdevice for the
# arbiter). Used by the tests in this directory.
set -euo pipefail

P=hlcfs
STATE=/tmp/${P}-nslab # BIN, DIR, node count and arbiter of the running lab, for the commands that take only a node
QNETD=192.0.2.10

conf() { # conf NODES QDEVICE
    local i
    cat <<'EOF'
totem {
  version: 2
  cluster_name: hlcfs-test
  transport: knet
  crypto_cipher: none
  crypto_hash: none
}
nodelist {
EOF
    for i in $(seq 1 "$1"); do
        printf '  node {\n    ring0_addr: 192.0.2.%s\n    nodeid: %s\n  }\n' "$i" "$i"
    done
    echo "}"
    if [ "$2" = 1 ]; then
        # The lab's arbiter speaks without TLS: certificates would prove nothing here, and the lab has no secrets.
        cat <<EOF
quorum {
  provider: corosync_votequorum
  wait_for_all: 1
  device {
    model: net
    votes: 1
    net {
      host: $QNETD
      algorithm: ffsplit
      tls: off
    }
  }
}
EOF
    else
        printf 'quorum {\n  provider: corosync_votequorum\n}\n'
    fi
    printf 'logging {\n  to_stderr: yes\n  to_syslog: no\n}\n'
}

lab_bin() { sed -n 1p $STATE; }
lab_dir() { sed -n 2p $STATE; }
lab_qdevice() { sed -n 4p $STATE 2> /dev/null || echo 0; }

alive() { # alive PIDFILE: the process it names is running
    [ -s "$1" ] && kill -0 "$(cat "$1")" 2> /dev/null
}

in_ns() { # in_ns DIR NAME command: run a shell command in the network and mount namespaces of NAME (nN, or q)
    nsenter -t "$(cat "$1/$2/holder.pid")" -m -n sh -c "$3"
}

in_node() { # in_node DIR N command
    in_ns "$1" "n$2" "$3"
}

kill_wait() { # kill_wait PIDFILE: SIGKILL the process and wait until it is gone
    local file=${1:?}
    if alive "$file"; then
        local pid
        pid=$(cat "$file")
        kill -9 "$pid" 2> /dev/null || true
        while kill -0 "$pid" 2> /dev/null; do sleep 0.05; done
    fi
    rm -f "$file"
}

start_node() { # start_node BIN DIR N: Corosync if it is not running, its QDevice client, then hyperlite-cfs
    local bin=$1 dir=$2 i=$3
    if ! alive "$dir/n$i/corosync.pid"; then
        in_node "$dir" "$i" "corosync -f >> '$dir/n$i/corosync.log' 2>&1 & echo \$! > '$dir/n$i/corosync.pid'"
        # hyperlite-cfs once Corosync answers on its local socket (cmap answers without the other nodes).
        in_node "$dir" "$i" "for _ in \$(seq 1 150); do corosync-cmapctl totem.cluster_name > /dev/null 2>&1 && break; sleep 0.2; done"
    fi
    if [ "$(lab_qdevice)" = 1 ] && ! alive "$dir/n$i/qdevice.pid"; then
        in_node "$dir" "$i" "corosync-qdevice -f >> '$dir/n$i/qdevice.log' 2>&1 & echo \$! > '$dir/n$i/qdevice.pid'"
    fi
    if ! alive "$dir/n$i/cfs.pid"; then
        in_node "$dir" "$i" "'$bin' --cluster --db '$dir/n$i/config.db' --socket '$dir/n$i/cfs.sock' --socket-mode 0666 \
            >> '$dir/n$i/cfs.log' 2>&1 & echo \$! > '$dir/n$i/cfs.pid'"
    fi
}

holder() { # holder DIR NAME NETNS: a process keeping a mount namespace for NAME, inside network namespace NETNS
    local dir=${1:?} name=${2:?}
    rm -f "${dir:?}/${name:?}/holder.pid"
    ip netns exec "$3" unshare -m sh -c "
        mount --bind '$dir/$name/lib' /var/lib/corosync
        mount --bind '$dir/$name/run' /run
        mount -t tmpfs tmpfs /dev/shm
        echo \$\$ > '$dir/$name/holder.pid'
        exec sleep infinity
    " > /dev/null 2>&1 &
    for _ in $(seq 1 100); do [ -s "$dir/$name/holder.pid" ] && break || sleep 0.05; done
}

start_qnetd() {
    local dir
    dir=$(lab_dir)
    alive "$dir/q/qnetd.pid" && return 0
    # Run as root with the TLS database unused (-s off); the package's own unit runs it as coroqnetd.
    in_ns "$dir" q "corosync-qnetd -f -s off -l $QNETD >> '$dir/q/qnetd.log' 2>&1 & echo \$! > '$dir/q/qnetd.pid'"
}

netns_veth() { # netns_veth NAME ADDRESS: a namespace NAME on the bridge with ADDRESS
    ip netns add "${P}$1"
    ip link add "${P}v$1" type veth peer name "${P}e$1"
    ip link set "${P}v$1" master ${P}br up
    ip link set "${P}e$1" netns "${P}$1"
    ip -n "${P}$1" addr add "$2/24" dev "${P}e$1"
    ip -n "${P}$1" link set "${P}e$1" up
    ip -n "${P}$1" link set lo up
}

up() {
    local bin dir=$2 nodes=${HYPERLITE_LAB_NODES:-3} qdevice=${HYPERLITE_LAB_QDEVICE:-0}
    bin=$(realpath "$1")
    printf '%s\n%s\n%s\n%s\n' "$bin" "$dir" "$nodes" "$qdevice" > $STATE
    ip link add ${P}br type bridge
    ip link set ${P}br up
    if [ "$qdevice" = 1 ]; then
        netns_veth q $QNETD
        mkdir -p "$dir/q/lib" "$dir/q/run"
        holder "$dir" q ${P}q
        start_qnetd
    fi
    for i in $(seq 1 "$nodes"); do
        netns_veth "$i" "192.0.2.$i"
        mkdir -p "/etc/netns/${P}$i/corosync" "$dir/n$i/lib" "$dir/n$i/run"
        conf "$nodes" "$qdevice" > "/etc/netns/${P}$i/corosync/corosync.conf"
        holder "$dir" "n$i" "${P}$i"
        start_node "$bin" "$dir" "$i"
    done
    # Hand the cluster over only once every daemon is a member of one process group.
    for _ in $(seq 1 120); do
        local n
        n=$(in_node "$dir" 1 corosync-cpgtool 2> /dev/null |
            awk '/^hyperlite-cfs/ { g = 1; next } /^[^[:space:]]/ { g = 0 } g && NF { n++ } END { print n + 0 }') || n=0
        [ "$n" -eq "$nodes" ] && return 0
        sleep 1
    done
    echo "lab: the $nodes daemons never formed one group" >&2
    return 1
}

down() { # every hlcfs namespace, whatever the size of the lab that made it
    local ns
    for ns in $(ip netns list | awk '{ print $1 }' | grep -E "^${P}([0-9]+|q)\$" || true); do
        ip netns pids "$ns" | xargs -r kill 2> /dev/null || true
        sleep 0.5
        ip netns pids "$ns" | xargs -r kill -9 2> /dev/null || true
        ip netns del "$ns"
        rm -rf "/etc/netns/${ns:?}"
    done
    ip link del ${P}br 2> /dev/null || true
    rm -f $STATE
}

case ${1:-} in
    up) up "$2" "$3" ;;
    down) down ;;
    cut) ip link set "${P}v$2" down ;;
    heal) ip link set "${P}v$2" up ;;
    stop) kill_wait "$(lab_dir)/n$2/cfs.pid" ;;
    crash)
        kill_wait "$(lab_dir)/n$2/qdevice.pid"
        kill_wait "$(lab_dir)/n$2/corosync.pid"
        kill_wait "$(lab_dir)/n$2/cfs.pid"
        ;;
    start) start_node "$(lab_bin)" "$(lab_dir)" "$2" ;;
    tool)
        n=$2
        shift 2
        in_node "$(lab_dir)" "$n" "$(printf '%q ' "$(lab_bin)" "$@") < /dev/null"
        ;;
    qnetd)
        case ${2:-} in
            stop) kill_wait "$(lab_dir)/q/qnetd.pid" ;;
            start) start_qnetd ;;
            *) exit 2 ;;
        esac
        ;;
    *)
        echo "usage: $0 up BIN DIR | cut N | heal N | stop N | crash N | start N | qnetd stop|start | tool N ARGS... | down" >&2
        exit 2
        ;;
esac
