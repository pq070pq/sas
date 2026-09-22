import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

// db.ts reads DATA_DIR at import time and opens a real sqlite file there, so
// point it at a throwaway directory instead of the app's real data/ folder.
let tmpDir: string;
let db: (typeof import("./db.js"))["db"];

beforeAll(async () => {
  tmpDir = mkdtempSync(join(tmpdir(), "bloomber-db-test-"));
  process.env.DATA_DIR = tmpDir;
  ({ db } = await import("./db.js"));
});

afterAll(() => {
  db.close();
  rmSync(tmpDir, { recursive: true, force: true });
  delete process.env.DATA_DIR;
});

describe("db foreign keys", () => {
  it("enforces the foreign key on transactions.portfolio_id", () => {
    expect(() =>
      db
        .prepare(
          "INSERT INTO transactions (portfolio_id, symbol, side, quantity, price, executed_at) VALUES (?, ?, ?, ?, ?, ?)"
        )
        .run(999999, "AAPL", "BUY", 1, 100, "2026-01-01T00:00:00Z")
    ).toThrow(/FOREIGN KEY constraint failed/i);
  });

  it("still allows inserts against a real portfolio", () => {
    const portfolio = db.prepare("SELECT id FROM portfolios LIMIT 1").get() as { id: number };
    expect(() =>
      db
        .prepare(
          "INSERT INTO transactions (portfolio_id, symbol, side, quantity, price, executed_at) VALUES (?, ?, ?, ?, ?, ?)"
        )
        .run(portfolio.id, "AAPL", "BUY", 1, 100, "2026-01-01T00:00:00Z")
    ).not.toThrow();
  });
});
