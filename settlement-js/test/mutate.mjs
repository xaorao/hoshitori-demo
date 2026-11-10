// A tiny mutation harness: import a copy of a source module with one rule deliberately broken.
import { cpSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export async function mutant(relativePath, oldText, newText) {
  const original = new URL(relativePath, import.meta.url);
  // The copy lives in a temporary directory, so its relative imports are pointed back at the original directory.
  const base = new URL(".", original).href;
  const source = readFileSync(fileURLToPath(original), "utf8").replaceAll('from "./', `from "${base}`);
  const count = source.split(oldText).length - 1;
  if (count !== 1) throw new Error(`mutation target occurs ${count} times: ${oldText}`);
  const dir = mkdtempSync(join(tmpdir(), "hoshitori-mutant-"));
  const file = join(dir, "mutant.mjs");
  writeFileSync(file, source.replace(oldText, newText));
  return import(pathToFileURL(file).href);
}

// Like `mutant`, but the whole source directory is copied so that a defect in one module is seen by the modules that import it.
// Returns the module named by `entry` (default: the mutated file itself).
export async function mutantTree(relativePath, oldText, newText, entry = relativePath) {
  const srcDir = fileURLToPath(new URL(".", new URL(`../${relativePath}`, import.meta.url)));
  const dir = mkdtempSync(join(tmpdir(), "hoshitori-tree-"));
  cpSync(srcDir, dir, { recursive: true });
  const target = join(dir, relativePath.split("/").pop());
  const source = readFileSync(target, "utf8");
  const count = source.split(oldText).length - 1;
  if (count !== 1) throw new Error(`mutation target occurs ${count} times in ${relativePath}: ${oldText}`);
  writeFileSync(target, source.replace(oldText, newText));
  return import(pathToFileURL(join(dir, entry.split("/").pop())).href);
}

// The mutated copy of the whole source directory, as a function that imports any module of it by name.
export function mutantDir(relativePath, oldText, newText) {
  const srcDir = fileURLToPath(new URL(".", new URL(`../${relativePath}`, import.meta.url)));
  const dir = mkdtempSync(join(tmpdir(), "hoshitori-tree-"));
  cpSync(srcDir, dir, { recursive: true });
  const target = join(dir, relativePath.split("/").pop());
  const source = readFileSync(target, "utf8");
  const count = source.split(oldText).length - 1;
  if (count !== 1) throw new Error(`mutation target occurs ${count} times in ${relativePath}: ${oldText}`);
  writeFileSync(target, source.replace(oldText, newText));
  return (name) => import(pathToFileURL(join(dir, name)).href);
}
