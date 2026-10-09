import { describe, expect, it } from "vitest";
import { codeFromHash } from "./screens/SetupCode";

describe("codeFromHash", () => {
  it("reads the code Setup.exe passes", () => {
    expect(codeFromHash("#setup=ABCD-EFGH-2345")).toBe("ABCD-EFGH-2345");
  });
  it("ignores anything else", () => {
    expect(codeFromHash("")).toBeNull();
    expect(codeFromHash("#setup=abcd-efgh-2345")).toBeNull();
    expect(codeFromHash("#setup=ABCD-EFGH-2345&x=1")).toBeNull();
    expect(codeFromHash("#other")).toBeNull();
  });
});
