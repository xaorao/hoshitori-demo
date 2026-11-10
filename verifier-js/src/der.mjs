// A strict DER reader for the structures of RFC 3161, CMS and X.509. Second implementation; no dependencies.
// Strict means: definite lengths only, the shortest length encoding, single-byte tags, no bytes outside the structure.
// A malformed input throws DerError; callers turn that into the rejection code PARSE.

export class DerError extends Error {}

export const TAG = Object.freeze({
  BOOLEAN: 0x01, INTEGER: 0x02, BIT_STRING: 0x03, OCTET_STRING: 0x04, NULL: 0x05, OID: 0x06, UTF8: 0x0c,
  UTC_TIME: 0x17, GENERALIZED_TIME: 0x18, SEQUENCE: 0x30, SET: 0x31,
});

const MAX_DEPTH = 32;

export class Element {
  constructor(tag, header, contents, raw, depth) {
    this.depth = depth; // 0 for the outermost element
    this.tag = tag; // the identifier octet
    this.header = header;
    this.contents = contents;
    this.raw = raw; // header and contents exactly as read
    this._children = null;
  }

  get constructed() {
    return (this.tag & 0x20) !== 0;
  }

  get contextNumber() {
    return (this.tag & 0xc0) === 0x80 ? this.tag & 0x1f : null;
  }

  // The children of a constructed element, parsed once.
  get children() {
    if (this._children === null) {
      if (!this.constructed) throw new DerError("a primitive element has no children");
      this._children = readAll(this.contents, this.depth + 1);
    }
    return this._children;
  }
}

function readElement(buf, offset, depth) {
  if (depth > MAX_DEPTH) throw new DerError("nesting too deep");
  if (offset + 2 > buf.length) throw new DerError("truncated element");
  const tag = buf[offset];
  if ((tag & 0x1f) === 0x1f) throw new DerError("multi-byte tags are not used here");
  let length = buf[offset + 1];
  let headerLength = 2;
  if (length === 0x80) throw new DerError("indefinite length");
  if (length > 0x80) {
    const count = length & 0x7f;
    if (count > 4 || offset + 2 + count > buf.length) throw new DerError("bad length of length");
    if (buf[offset + 2] === 0) throw new DerError("length is not minimal");
    length = 0;
    for (let i = 0; i < count; i++) length = length * 256 + buf[offset + 2 + i];
    if (length < 0x80) throw new DerError("length is not minimal");
    headerLength = 2 + count;
  }
  const start = offset + headerLength;
  const end = start + length;
  if (end > buf.length) throw new DerError("element runs past the end");
  return { element: new Element(tag, buf.subarray(offset, start), buf.subarray(start, end), buf.subarray(offset, end), depth), end };
}

function readAll(buf, depth) {
  const out = [];
  let offset = 0;
  while (offset < buf.length) {
    const { element, end } = readElement(buf, offset, depth);
    out.push(element);
    offset = end;
  }
  return out;
}

// One element that fills the whole buffer.
export function parseExact(buf) {
  const { element, end } = readElement(buf, 0, 0);
  if (end !== buf.length) throw new DerError("trailing data");
  return element;
}

export function expectTag(element, tag, what) {
  if (!element || element.tag !== tag) throw new DerError(`${what}: unexpected tag`);
  return element;
}

export function oid(element) {
  expectTag(element, TAG.OID, "object identifier");
  const bytes = element.contents;
  if (bytes.length === 0) throw new DerError("empty object identifier");
  const arcs = [];
  let value = 0n;
  let first = true;
  for (let i = 0; i < bytes.length; i++) {
    const byte = bytes[i];
    if (value === 0n && byte === 0x80) throw new DerError("object identifier arc is not minimal");
    value = (value << 7n) | BigInt(byte & 0x7f);
    if ((byte & 0x80) === 0) {
      if (first) {
        const top = value < 80n ? value / 40n : 2n;
        arcs.push(top, value - top * 40n);
        first = false;
      } else {
        arcs.push(value);
      }
      value = 0n;
    } else if (i === bytes.length - 1) {
      throw new DerError("truncated object identifier");
    }
  }
  return arcs.join(".");
}

export function integer(element) {
  expectTag(element, TAG.INTEGER, "integer");
  const bytes = element.contents;
  if (bytes.length === 0) throw new DerError("empty integer");
  if (bytes.length > 1 && ((bytes[0] === 0 && (bytes[1] & 0x80) === 0) || (bytes[0] === 0xff && (bytes[1] & 0x80) !== 0))) {
    throw new DerError("integer is not minimal");
  }
  let value = 0n;
  for (const byte of bytes) value = (value << 8n) | BigInt(byte);
  return (bytes[0] & 0x80) !== 0 ? value - (1n << BigInt(8 * bytes.length)) : value;
}

export function boolean(element) {
  expectTag(element, TAG.BOOLEAN, "boolean");
  if (element.contents.length !== 1 || (element.contents[0] !== 0 && element.contents[0] !== 0xff)) throw new DerError("bad boolean");
  return element.contents[0] === 0xff;
}

export function octetString(element) {
  expectTag(element, TAG.OCTET_STRING, "octet string");
  return element.contents;
}

// The bytes of a BIT STRING and the number of unused bits.
export function bitString(element) {
  expectTag(element, TAG.BIT_STRING, "bit string");
  const bytes = element.contents;
  if (bytes.length === 0 || bytes[0] > 7) throw new DerError("bad bit string");
  if (bytes.length === 1 && bytes[0] !== 0) throw new DerError("bad bit string");
  // DER: the unused bits of the last byte are zero (a parser that re-encodes would repair them, and the repaired value would verify)
  if (bytes.length > 1 && (bytes[bytes.length - 1] & ((1 << bytes[0]) - 1)) !== 0) throw new DerError("a bit string has padding bits that are set");
  return { bytes: bytes.subarray(1), unused: bytes[0] };
}

const TIME_UTC = /^(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)Z$/;
const TIME_GENERALIZED = /^(\d{4})(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(?:\.(\d+))?Z$/;

function epochMs(year, month, day, hour, minute, second, fraction) {
  const ms = Date.UTC(year, month - 1, day, hour, minute, second);
  const check = new Date(ms);
  if (check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day || hour > 23 || minute > 59 || second > 59) {
    throw new DerError("time out of range");
  }
  return ms + (fraction ? Number(`0.${fraction}`.slice(0, 5)) * 1000 | 0 : 0);
}

// UTCTime or GeneralizedTime in UTC, as milliseconds since the epoch.
export function time(element) {
  const text = element.contents.toString("latin1");
  if (element.tag === TAG.UTC_TIME) {
    const m = TIME_UTC.exec(text);
    if (!m) throw new DerError("bad UTCTime");
    const yy = Number(m[1]);
    return epochMs(yy >= 50 ? 1900 + yy : 2000 + yy, Number(m[2]), Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6]), "");
  }
  if (element.tag === TAG.GENERALIZED_TIME) {
    const m = TIME_GENERALIZED.exec(text);
    if (!m) throw new DerError("bad GeneralizedTime");
    return epochMs(Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6]), m[7] ?? "");
  }
  throw new DerError("not a time");
}

// Walk every constructed element below `element`, so that a defect anywhere in the structure is found, not only on the paths
// that are read. Primitive contents (octet strings that hold DER, keys) are checked by whoever reads them.
export function validateTree(element) {
  if (element.constructed) for (const child of element.children) validateTree(child);
}
