import { afterEach, describe, expect, it, vi } from "vitest";
import { latest, series } from "./ecb.js";

function mockFetchOnce(csv: string) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, text: async () => csv });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ecb provider CSV parsing", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("locates TIME_PERIOD/OBS_VALUE by header name, not fixed index", async () => {
    // FM (key interest rates) has TIME_PERIOD/OBS_VALUE at columns 8/9...
    const fm = [
      "KEY,FREQ,REF_AREA,CURRENCY,PROVIDER_FM,INSTRUMENT_FM,PROVIDER_FM_ID,DATA_TYPE_FM,TIME_PERIOD,OBS_VALUE,OBS_STATUS",
      "FM.D.U2.EUR.4F.KR.DFR.LEV,D,U2,EUR,4F,KR,DFR,LEV,2026-09-20,2.5,A",
    ].join("\n");
    mockFetchOnce(fm);
    expect(await latest("FM", "D.U2.EUR.4F.KR.DFR.LEV")).toEqual({ date: "2026-09-20", value: 2.5 });

    // ...while ICP (inflation) has one fewer dimension column, so they land at 7/8 instead.
    // A fixed-index parser would silently read the wrong cell here.
    const icp = [
      "KEY,FREQ,REF_AREA,ADJUSTMENT,ICP_ITEM,STS_INSTITUTION,ICP_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS",
      "ICP.M.U2.N.000000.4.ANR,M,U2,N,000000,4,ANR,2025-12,1.9,A",
    ].join("\n");
    mockFetchOnce(icp);
    expect(await latest("ICP", "M.U2.N.000000.4.ANR")).toEqual({ date: "2025-12", value: 1.9 });
  });

  it("skips rows with a non-numeric OBS_VALUE and returns only the last N points", async () => {
    const csv = [
      "KEY,FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE",
      "X,D,U2,2026-09-18,3.1",
      "X,D,U2,2026-09-19,", // provisional/missing observation — common in ECB series
      "X,D,U2,2026-09-20,3.3",
    ].join("\n");
    mockFetchOnce(csv);
    const points = await series("FM", "D.U2.X", 5);
    expect(points).toEqual([
      { date: "2026-09-18", value: 3.1 },
      { date: "2026-09-20", value: 3.3 },
    ]);
  });

  it("returns an empty array when the dataflow has no rows", async () => {
    mockFetchOnce("KEY,FREQ,REF_AREA,TIME_PERIOD,OBS_VALUE\n");
    expect(await series("FM", "D.U2.X")).toEqual([]);
    expect(await latest("FM", "D.U2.X")).toBeNull();
  });

  it("throws with the flow/key on a non-OK response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 504 }));
    await expect(series("FM", "D.U2.X")).rejects.toThrow("ecb 504 for FM/D.U2.X");
  });
});
