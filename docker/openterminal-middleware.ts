import { NextRequest, NextResponse } from "next/server";

const SECRET = process.env.SAS_TERMINAL_SECRET ?? "";
const COOKIE = "sas_terminal_access";
const TERMINAL_PATH = "/terminal";

function base64UrlToBytes(value: string): Uint8Array {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function hmacSha256(secret: string, body: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  return base64UrlEncode(new Uint8Array(signature));
}

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function decodeToken(token: string) {
  if (!SECRET) return null;
  const [body, sig] = token.split(".");
  if (!body || !sig) return null;

  const expected = await hmacSha256(SECRET, body);
  if (!safeEqual(sig, expected)) return null;

  try {
    const data = JSON.parse(new TextDecoder().decode(base64UrlToBytes(body)));
    if (!data.exp || Number(data.exp) <= Math.floor(Date.now() / 1000)) return null;
    return data;
  } catch {
    return null;
  }
}

export async function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;
  const token = req.nextUrl.searchParams.get("sas_token") ?? req.cookies.get(COOKIE)?.value ?? "";
  const data = await decodeToken(token);

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
