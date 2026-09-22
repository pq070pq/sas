/** Third-party feeds (news, etc.) can return arbitrary URL schemes (`javascript:`,
 * `data:`, ...) in link fields. Only http(s) URLs are safe to use as an <a href>. */
export function isSafeHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}
