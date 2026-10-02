/* `hyperlite-cfs expected-votes`: the counterpart of Proxmox's `pvecm expected`, for a partition that lost its quorum
 * because the nodes it misses are down for good (design, sections 5 and 8.1 step B4). It refuses on a quorate
 * partition and when the votes asked for would not make this one quorate, lists the configured nodes that are missing,
 * says what the override risks, and changes nothing until the cluster's name is typed (or given with --confirm). */

#ifndef CFS_EXPECTED_H
#define CFS_EXPECTED_H

int cfs_expected_main(int argc, char **argv);

#endif
