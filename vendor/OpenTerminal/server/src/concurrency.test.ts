import { describe, expect, it } from "vitest";
import { mapWithConcurrency } from "./concurrency.js";

describe("mapWithConcurrency", () => {
  it("preserves result order regardless of completion order", async () => {
    const delays = [30, 10, 20, 0, 15];
    const results = await mapWithConcurrency(delays, 2, (ms, i) => new Promise<number>((r) => setTimeout(() => r(i), ms)));
    expect(results).toEqual([0, 1, 2, 3, 4]);
  });

  it("never runs more than `limit` calls at once", async () => {
    let active = 0;
    let maxActive = 0;
    const items = Array.from({ length: 20 }, (_, i) => i);
    await mapWithConcurrency(items, 3, async (i) => {
      active++;
      maxActive = Math.max(maxActive, active);
      await new Promise((r) => setTimeout(r, 5));
      active--;
      return i;
    });
    expect(maxActive).toBeLessThanOrEqual(3);
  });

  it("handles an empty list", async () => {
    const results = await mapWithConcurrency([] as number[], 5, async (i) => i);
    expect(results).toEqual([]);
  });
});
