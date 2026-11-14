import { describe, expect, it } from "vitest";
import { parseQuery, runQuery } from "../next/lib/query";

const objects = [
  { kind: "vm", name: "web-1", state: "active", node: "local", nodeName: "antho", tags: ["prod"], pools: ["web"], vcpu: 4, memory_mb: 8192, os: "Debian 12", ip: "10.0.0.5" },
  { kind: "vm", name: "web-2", state: "stopped", node: "local", nodeName: "antho", tags: ["dev"], pools: ["web"], vcpu: 2, memory_mb: 2048, os: "Debian 12", ip: "" },
  { kind: "vm", name: "db-1", state: "active", node: "n2", nodeName: "n2", tags: ["prod", "db"], pools: [], vcpu: 8, memory_mb: 32768, os: "Rocky 9", ip: "10.0.0.9" },
  { kind: "container", name: "proxy", state: "active", node: "local", nodeName: "antho", tags: [], pools: [], vcpu: 1, memory_mb: 512 },
];
const names = (q) => runQuery(objects, q).found.map((o) => o.name);

describe("query search", () => {
  it("narrows by every term", () => {
    expect(names("kind:vm state:running tag:prod")).toEqual(["web-1", "db-1"]);
    expect(names("pool:web state:stopped")).toEqual(["web-2"]);
    expect(names("vcpu>=4 mem>16")).toEqual(["db-1"]);
    expect(names("-tag:prod kind:vm")).toEqual(["web-2"]);
    expect(names("node:n2")).toEqual(["db-1"]);
    expect(names("os:debian web")).toEqual(["web-1", "web-2"]);
    expect(names("name:web-*")).toEqual(["web-1", "web-2"]);
    expect(names("kind:ct")).toEqual(["proxy"]);
    expect(names("ip:10.0.0")).toEqual(["web-1", "db-1"]);
  });
  it("says what it does not understand", () => {
    expect(parseQuery("colour:red state>2 vcpu>x").errors).toEqual(["colour", "state>", "vcpu>x"]);
    expect(parseQuery('pool:"my pool"').terms[0].value).toBe("my pool");
  });
});
