import { describe, expect, it } from "vitest";
import { cached, cacheGet, cacheSet, staleGet, staleSet } from "./cache.js";

describe("cache", () => {
  it("stores and retrieves values within TTL", () => {
    cacheSet("k1", 42, 1000);
    expect(cacheGet("k1")).toBe(42);
  });

  it("expires values after TTL", async () => {
    cacheSet("k2", "x", 1);
    await new Promise((r) => setTimeout(r, 10));
    expect(cacheGet("k2")).toBeUndefined();
  });

  it("serves stale data when the loader fails after a prior success", async () => {
    let calls = 0;
    const loader = async () => {
      calls++;
      if (calls === 1) return "fresh";
      throw new Error("provider down");
    };
    expect(await cached("k3", 1, loader)).toBe("fresh");
    await new Promise((r) => setTimeout(r, 10));
    expect(await cached("k3", 1, loader)).toBe("fresh"); // stale fallback
    expect(calls).toBe(2);
  });

  it("propagates errors when no stale data exists", async () => {
    await expect(cached("k4", 1000, async () => Promise.reject(new Error("boom")))).rejects.toThrow("boom");
  });

  it("evicts the oldest entries once the cache exceeds its max size", () => {
    for (let i = 0; i < 1001; i++) {
      cacheSet(`flood-${i}`, i, 60_000);
    }
    expect(cacheGet("flood-0")).toBeUndefined();
    expect(cacheGet("flood-1000")).toBe(1000);
  });

  it("evicts the oldest stale entries once staleStore exceeds its max size", () => {
    for (let i = 0; i < 1001; i++) {
      staleSet(`stale-flood-${i}`, i);
    }
    expect(staleGet("stale-flood-0")).toBeUndefined();
    expect(staleGet("stale-flood-1000")).toBe(1000);
  });
});
