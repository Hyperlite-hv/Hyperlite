import { describe, expect, it } from "vitest";
import { poolFormErrors } from "../next/pages/StoragePage";

const base = { name: "pool-1", type: "dir", node: "local", path: "", nfs_host: "", nfs_export_path: "", nfs_version: "4.2", size_gb: "20", iscsi_host: "", iscsi_port: "3260", iscsi_target: "", chap_user: "", chap_password: "" };

describe("pool form: the same rules as the API, for the fields each kind uses", () => {
  it("a directory pool needs a name only; its path, when given, is absolute", () => {
    expect(poolFormErrors(base)).toEqual({});
    expect(poolFormErrors({ ...base, name: "" })).toEqual({ name: "stor.e.required" });
    expect(poolFormErrors({ ...base, name: "a b" })).toEqual({ name: "stor.e.name" });
    expect(poolFormErrors({ ...base, path: "data/vms" })).toEqual({ path: "stor.e.absPath" });
  });
  it("an NFS share is a server AND an exported path", () => {
    const nfs = { ...base, type: "netfs" };
    expect(poolFormErrors(nfs)).toEqual({ nfs_host: "stor.e.required", nfs_export_path: "stor.e.required" });
    expect(poolFormErrors({ ...nfs, nfs_host: "nas.lan" })).toEqual({ nfs_export_path: "stor.e.required" });
    expect(poolFormErrors({ ...nfs, nfs_host: "nas lan", nfs_export_path: "/srv" })).toEqual({ nfs_host: "stor.e.host" });
    expect(poolFormErrors({ ...nfs, nfs_host: "nas.lan", nfs_export_path: "/srv/vms" })).toEqual({});
  });
  it("ZFS is created on this host only, with a size", () => {
    const zfs = { ...base, type: "zfs" };
    expect(poolFormErrors(zfs)).toEqual({});
    expect(poolFormErrors({ ...zfs, node: "antho" })).toEqual({ type: "stor.e.zfsLocal" });
    expect(poolFormErrors({ ...zfs, size_gb: "0" })).toEqual({ size_gb: "stor.e.size" });
  });
  it("iSCSI: a portal, a port, an IQN, and CHAP user and password together or neither", () => {
    const ok = { ...base, type: "iscsi", iscsi_host: "192.168.1.20", iscsi_target: "iqn.2005-10.org.freenas.ctl:vms" };
    expect(poolFormErrors(ok)).toEqual({});
    expect(poolFormErrors({ ...ok, iscsi_port: "70000" })).toEqual({ iscsi_port: "stor.e.port" });
    expect(poolFormErrors({ ...ok, iscsi_target: "vms" })).toEqual({ iscsi_target: "stor.iscsiTargetBad" });
    expect(poolFormErrors({ ...ok, chap_user: "hyper" })).toEqual({ chap_password: "stor.e.chapBoth" });
    expect(poolFormErrors({ ...ok, chap_password: "x" })).toEqual({ chap_user: "stor.e.chapBoth" });
    expect(poolFormErrors({ ...ok, chap_user: "a b", chap_password: "x" })).toEqual({ chap_user: "stor.e.chapUser" });
    expect(poolFormErrors({ ...ok, chap_user: "hyper", chap_password: "x" })).toEqual({});
  });
});
