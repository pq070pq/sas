import type { NextFunction, Request, Response } from "express";

/** Minimal in-memory fixed-window limiter — good enough for a single-process,
 * single-instance deployment. Protects the paid AI endpoint from being
 * hammered even by a caller that does have a valid API key. */
export function rateLimit({ windowMs, max }: { windowMs: number; max: number }) {
  const hits = new Map<string, { count: number; resetAt: number }>();

  // Without this sweep, a key that's seen once and never again (e.g. a forged
  // X-Forwarded-For under TRUST_PROXY=1) would sit in `hits` forever.
  const sweep = setInterval(() => {
    const now = Date.now();
    for (const [key, entry] of hits) {
      if (now > entry.resetAt) hits.delete(key);
    }
  }, windowMs);
  sweep.unref();

  const middleware = (req: Request, res: Response, next: NextFunction): void => {
    const key = req.ip ?? "unknown";
    const now = Date.now();
    const entry = hits.get(key);

    if (!entry || now > entry.resetAt) {
      hits.set(key, { count: 1, resetAt: now + windowMs });
      next();
      return;
    }

    if (entry.count >= max) {
      const retryAfter = Math.ceil((entry.resetAt - now) / 1000);
      res.setHeader("Retry-After", String(retryAfter));
      res.status(429).json({ error: "rate limit exceeded, try again shortly" });
      return;
    }

    entry.count += 1;
    next();
  };

  // Exposed for tests only; not part of the public middleware contract.
  Object.defineProperty(middleware, "_hits", { value: hits });

  return middleware;
}
