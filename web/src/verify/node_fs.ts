// The part of node:fs the offline verifier uses, over files held in memory: a page has no file system, so the bundle's files are
// fetched and handed in. Errors carry the same `code` node:fs gives (ENOENT, ENOTDIR, EISDIR, EBADF), because the verifier reports it.
// The verifier reads a bundle file through a descriptor (openSync, fstatSync, readSync, closeSync) so that it can refuse anything but a
// regular file and never read past a limit; here every file is regular and in memory, and the open flags have nothing to change.

const files = new Map<string, Uint8Array>();
const directories = new Set<string>();
const descriptors = new Map<number, { data: Uint8Array | null; position: number }>(); // data is null for a directory
let nextDescriptor = 3;

function normal(path: string): string {
  const parts: string[] = [];
  for (const part of path.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return `/${parts.join("/")}`;
}

function failure(code: string, syscall: string, path: string): Error {
  const error = new Error(`${code}: ${syscall} '${path}'`) as Error & { code: string };
  error.code = code;
  return error;
}

/** Replace what the verifier can see: files by path, and any directories that have no file in them. */
export function setFiles(entries: Record<string, Uint8Array>, emptyDirectories: string[] = []): void {
  files.clear();
  directories.clear();
  descriptors.clear();
  directories.add("/");
  const add = (dir: string): void => {
    for (let at = dir; at !== "/"; at = at.slice(0, at.lastIndexOf("/")) || "/") directories.add(at);
  };
  for (const [path, data] of Object.entries(entries)) {
    const p = normal(path);
    files.set(p, data);
    add(p.slice(0, p.lastIndexOf("/")) || "/");
  }
  for (const dir of emptyDirectories) add(normal(dir));
}

export class Stats {
  readonly size: number;
  readonly #directory: boolean;
  constructor(directory: boolean, size: number) {
    this.#directory = directory;
    this.size = size;
  }
  isDirectory(): boolean {
    return this.#directory;
  }
  isFile(): boolean {
    return !this.#directory;
  }
}

export function statSync(path: string): Stats {
  const p = normal(path);
  const file = files.get(p);
  if (file !== undefined) return new Stats(false, file.length);
  if (directories.has(p)) return new Stats(true, 0);
  throw failure("ENOENT", "stat", path);
}

export function existsSync(path: string): boolean {
  const p = normal(path);
  return files.has(p) || directories.has(p);
}

export function readdirSync(path: string): string[] {
  const p = normal(path);
  if (files.has(p)) throw failure("ENOTDIR", "scandir", path);
  if (!directories.has(p)) throw failure("ENOENT", "scandir", path);
  const prefix = p === "/" ? "/" : `${p}/`;
  const names = new Set<string>();
  for (const key of [...files.keys(), ...directories]) {
    if (key.startsWith(prefix) && key !== p) names.add(key.slice(prefix.length).split("/")[0] as string);
  }
  return [...names];
}

export function readFileSync(path: string): Buffer;
export function readFileSync(path: string, encoding: "utf8"): string;
export function readFileSync(path: string, encoding?: "utf8"): Buffer | string {
  const p = normal(path);
  const data = files.get(p);
  if (data === undefined) throw failure(directories.has(p) ? "EISDIR" : "ENOENT", "open", path);
  const copy = Buffer.from(data);
  return encoding === "utf8" ? copy.toString("utf8") : copy;
}

// The open flags the verifier passes (read only, without blocking, no controlling terminal); in memory they change nothing.
export const constants = { O_RDONLY: 0, O_NONBLOCK: 0x4, O_NOCTTY: 0x20000 };

/** Open a file or a directory for reading, as POSIX does; the verifier refuses a directory from its descriptor. */
export function openSync(path: string, _flags?: number): number {
  const p = normal(path);
  const data = files.get(p);
  if (data === undefined && !directories.has(p)) throw failure("ENOENT", "open", path);
  const descriptor = nextDescriptor++;
  descriptors.set(descriptor, { data: data ?? null, position: 0 });
  return descriptor;
}

function opened(descriptor: number, syscall: string): { data: Uint8Array | null; position: number } {
  const entry = descriptors.get(descriptor);
  if (entry === undefined) throw failure("EBADF", syscall, String(descriptor));
  return entry;
}

export function fstatSync(descriptor: number): Stats {
  const entry = opened(descriptor, "fstat");
  return entry.data === null ? new Stats(true, 0) : new Stats(false, entry.data.length);
}

/** Copy up to `length` bytes from the current position into `buffer` at `offset` (the position argument is not used: always null). */
export function readSync(descriptor: number, buffer: Uint8Array, offset: number, length: number, _position: number | null): number {
  const entry = opened(descriptor, "read");
  if (entry.data === null) throw failure("EISDIR", "read", String(descriptor));
  const count = Math.max(0, Math.min(length, entry.data.length - entry.position, buffer.length - offset));
  buffer.set(entry.data.subarray(entry.position, entry.position + count), offset);
  entry.position += count;
  return count;
}

export function closeSync(descriptor: number): void {
  opened(descriptor, "close");
  descriptors.delete(descriptor);
}
