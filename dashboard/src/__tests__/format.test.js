import { describe, expect, it } from "vitest";
import { formatGo, formatKbps, formatMo, formatUptime } from "../utils/format";

describe("format helpers", () => {
  it("formats memory in MB or GB", () => {
    expect(formatMo(512)).toBe("512 MB");
    expect(formatMo(2048)).toBe("2.0 GB");
    expect(formatMo(null)).toBe("--");
  });
  it("formats disk sizes", () => {
    expect(formatGo(12.34)).toBe("12.3 GB");
    expect(formatGo(undefined)).toBe("--");
  });
  it("formats throughput", () => {
    expect(formatKbps(200)).toBe("200 KB/s");
    expect(formatKbps(2048)).toBe("2.0 MB/s");
  });
  it("formats uptime", () => {
    expect(formatUptime(0)).toBe("--");
    expect(formatUptime(90)).toBe("1m");
    expect(formatUptime(3700)).toBe("1h 1m");
    expect(formatUptime(90000)).toBe("1d 1h");
  });
});
