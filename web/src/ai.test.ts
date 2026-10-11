import { describe, expect, it } from "vitest";
import { citedHtml, citeNumbers } from "./components/Ai";
import { href, parse } from "./router";

describe("citations", () => {
  it("reads single numbers, lists and ranges", () => {
    expect(citeNumbers("3")).toEqual([3]);
    expect(citeNumbers("2, 4")).toEqual([2, 4]);
    expect(citeNumbers("5–7")).toEqual([5, 6, 7]);
  });

  it("links citations to sources that were sent, and only those", () => {
    const html = citedHtml("Due in March [1][2]. See [9]. Code `[1]` stays.", 2);
    expect(html).toContain('data-cite="1"');
    expect(html).toContain('data-cite="2"');
    expect(html).not.toContain('data-cite="9"');
    expect(html).toContain("[9]");
  });

  it("never lets the answer add its own markup", () => {
    const html = citedHtml('<img src=x onerror="alert(1)"> [1]', 1);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
});

describe("routes", () => {
  const id = "a".repeat(32);
  const file = "b".repeat(32);
  it("has Ask, Review and Settings pages", () => {
    expect(parse(`/cases/${id}/ask`, "")).toEqual({ page: "case", id, tab: "ask", folder: "" });
    expect(parse(`/cases/${id}/review`, `?file=${file}`)).toEqual({ page: "case", id, tab: "review", folder: "", file });
    expect(href({ page: "case", id, tab: "review", folder: "", file })).toBe(`/cases/${id}/review?file=${file}`);
    expect(parse("/settings", "")).toEqual({ page: "settings" });
  });
});
