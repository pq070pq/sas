import { NextRequest, NextResponse } from "next/server";
import crypto from "node:crypto";

const SECRET = process.env.SAS_TERMINAL_SECRET ?? "";
const COOKIE = "sas_terminal_access";
const TERMINAL_PATH = "/terminal";

function decodeToken(token: string) {
  if (!SECRET) return null;
  const [body, sig] = token.split(".");
  if (!body || !sig) return null;
  const expected = crypto.createHmac("sha256", SECRET).update(body).digest("base64url");
  if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) return null;
  try {
    const data = JSON.parse(Buffer.from(body, "base64url").toString("utf8"));
    if (!data.exp || Number(data.exp) <= Math.floor(Date.now() / 1000)) return null;
    return data;
  } catch {
    return null;
  }
}

export function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;
  const token = req.nextUrl.searchParams.get("sas_token") ?? req.cookies.get(COOKIE)?.value ?? "";
  const data = decodeToken(token);
  if (!data) {
    return new NextResponse("SAS PRO: يجب فتح المحطة من داخل Mini App باشتراك فعال.", {
      status: 401,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }

  const res = NextResponse.next();
  if (req.nextUrl.searchParams.has("sas_token")) {
    const clean = req.nextUrl.clone();
    clean.searchParams.delete("sas_token");
    const redirect = NextResponse.redirect(clean);
    redirect.cookies.set(COOKIE, token, {
      httpOnly: true,
      secure: true,
      sameSite: "lax",
      path: "/",
      maxAge: Math.max(60, Number(data.exp) - Math.floor(Date.now() / 1000)),
    });
    return redirect;
  }
  return res;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
