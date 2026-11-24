import { describe, expect, it } from "vitest";

// An effect written as `useEffect(() => el.scrollIntoView())` returns what the
// call returns. Newer Chromium (and so the desktop app) returns a Promise from
// scrollIntoView, which React then tries to call as the clean-up, and the page
// goes blank. Effects must use a block body, or return a clean-up function.
const sources = import.meta.glob<string>(["./**/*.tsx", "./**/*.ts", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true });

describe("effects", () => {
  it("never return the value of an expression", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(10);
    const bad = Object.entries(sources).flatMap(([file, text]) =>
      text.split("\n").flatMap((line, i) => (/useEffect\(\(\) => (?![{(])/.test(line) ? [`${file}:${i + 1}`] : [])),
    );
    expect(bad).toEqual([]);
  });
});
