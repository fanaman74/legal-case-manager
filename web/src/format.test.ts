import { describe, expect, it } from "vitest";
import { extensionOf, formatBytes, plural, shortHash } from "./format";
import { href, parse } from "./router";

describe("format", () => {
  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(250 * 1024 * 1024)).toBe("250 MB");
  });
  it("pluralises", () => {
    expect(plural(1, "file")).toBe("1 file");
    expect(plural(1200, "file")).toMatch(/^1.200 files$/);
  });
  it("shortens hashes", () => {
    expect(shortHash("a".repeat(64))).toBe("aaaaaaaa…aaaaaaaa");
  });
  it("reads extensions", () => {
    expect(extensionOf("Letter.PDF")).toBe(".pdf");
    expect(extensionOf(".hidden")).toBe("");
    expect(extensionOf("README")).toBe("");
  });
});

describe("router", () => {
  const id = "0123456789abcdef0123456789abcdef";
  it("round-trips case addresses", () => {
    const r = parse(`/cases/${id}`, "?folder=Pleadings%2FExhibits");
    expect(r).toEqual({ page: "case", id, tab: "files", folder: "Pleadings/Exhibits" });
    expect(href(r)).toBe(`/cases/${id}?folder=Pleadings%2FExhibits`);
    expect(parse(`/cases/${id}/people`, "")).toEqual({ page: "case", id, tab: "people", folder: "" });
  });
  it("rejects unknown pages", () => {
    expect(parse("/cases/nope", "").page).toBe("not-found");
    expect(parse(`/cases/${id}/secret`, "").page).toBe("not-found");
    expect(parse("/", "").page).toBe("cases");
  });
});
