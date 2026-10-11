import { describe, expect, it } from "vitest";
import { renderMarkdown, splitPages } from "./markdown";
import { href, parse } from "./router";

const C = "a".repeat(32);
const F = "b".repeat(32);

describe("document routes", () => {
  it("round-trips a document link with its page", () => {
    const route = { page: "document" as const, id: C, fileId: F, page_no: 4 };
    const url = href(route);
    const [path, query = ""] = url.split("?");
    expect(parse(path, query ? `?${query}` : "")).toEqual(route);
  });
  it("defaults to page 1 and parses the jobs page", () => {
    expect(parse(`/cases/${C}/files/${F}`, "")).toMatchObject({ page: "document", page_no: 1 });
    expect(parse("/jobs", "")).toEqual({ page: "jobs" });
  });
});

describe("markdown", () => {
  it("splits on page markers", () => {
    const parts = splitPages("<!-- page: 1 -->\n\nOne\n\n<!-- page: 2 -->\n\nTwo\n");
    expect(parts.map((p) => [p.page, p.text.trim()])).toEqual([[1, "One"], [2, "Two"]]);
  });
  it("never renders raw HTML from a document", () => {
    const html = renderMarkdown("<script>alert(1)</script> [x](javascript:alert(1)) [ok](https://example.com)");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain('href="javascript');
    expect(html).toContain('href="https://example.com" target="_blank" rel="noopener noreferrer"');
  });
});
