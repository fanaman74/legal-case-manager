import { describe, expect, it } from "vitest";
import { ago, bytes, duration, elapsed } from "./format";

describe("format", () => {
  it("formats durations compactly", () => {
    expect(duration(45_000)).toBe("45s");
    expect(duration(12 * 60_000)).toBe("12m");
    expect(duration(3 * 3600_000 + 20 * 60_000)).toBe("3h 20m");
    expect(duration(4 * 86400_000 + 6 * 3600_000)).toBe("4d 6h");
    expect(duration(-5)).toBe("0s");
  });
  it("formats relative time", () => {
    const now = Date.parse("2026-09-29T10:00:10Z");
    expect(ago("2026-09-29T10:00:09Z", now)).toBe("just now");
    expect(ago("2026-09-29T09:59:40Z", now)).toBe("30s ago");
    expect(ago(undefined, now)).toBe("");
  });
  it("formats bytes", () => {
    expect(bytes(512)).toBe("512 B");
    expect(bytes(1536)).toBe("1.5 KB");
    expect(bytes(412 * 1024 * 1024)).toBe("412 MB");
  });
  it("formats elapsed", () => {
    expect(elapsed("2026-09-29T10:00:00Z", Date.parse("2026-09-29T10:01:05Z"))).toBe("1:05");
  });
});
