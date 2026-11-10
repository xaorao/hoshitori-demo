// The part of node:path the verifier uses: a POSIX join.

export function join(...parts: string[]): string {
  const joined = parts.filter((p) => p !== "").join("/");
  if (joined === "") return ".";
  const absolute = joined.startsWith("/");
  const out: string[] = [];
  for (const part of joined.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (out.length > 0 && out[out.length - 1] !== "..") out.pop();
      else if (!absolute) out.push("..");
    } else out.push(part);
  }
  const text = out.join("/");
  return absolute ? `/${text}` : text === "" ? "." : text;
}
