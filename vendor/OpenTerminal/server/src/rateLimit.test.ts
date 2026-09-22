import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { rateLimit } from "./rateLimit.js";
import type { NextFunction, Request, Response } from "express";

function fakeReq(ip: string): Request {
  return { ip } as unknown as Request;
}

function fakeRes(): Response {
  return {
    setHeader: vi.fn(),
    status: vi.fn().mockReturnThis(),
    json: vi.fn().mockReturnThis(),
  } as unknown as Response;
}

describe("rateLimit", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("allows requests under the limit and blocks over it", () => {
    const limiter = rateLimit({ windowMs: 60_000, max: 2 });
    const next = vi.fn() as NextFunction;
    const res = fakeRes();

    limiter(fakeReq("1.1.1.1"), res, next);
    limiter(fakeReq("1.1.1.1"), res, next);
    expect(next).toHaveBeenCalledTimes(2);

    limiter(fakeReq("1.1.1.1"), res, next);
    expect(next).toHaveBeenCalledTimes(2);
    expect(res.status).toHaveBeenCalledWith(429);
  });

  it("sweeps expired entries out of the internal hits map so it does not grow unbounded", () => {
    const windowMs = 60_000;
    const limiter = rateLimit({ windowMs, max: 10 }) as unknown as ((
      req: Request,
      res: Response,
      next: NextFunction
    ) => void) & { _hits: Map<string, { count: number; resetAt: number }> };
    const next = vi.fn() as NextFunction;
    const res = fakeRes();

    for (let i = 0; i < 50; i++) {
      limiter(fakeReq(`10.0.0.${i}`), res, next);
    }
    expect(limiter._hits.size).toBe(50);

    // Advance past two sweep cycles: the first sweep tick lands exactly at each
    // entry's resetAt (not yet expired), the second is comfortably past it.
    vi.advanceTimersByTime(windowMs * 2 + 1);

    expect(limiter._hits.size).toBe(0);
  });
});
