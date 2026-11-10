// RFC 3339 timestamps with an explicit offset, as milliseconds since the epoch. Second implementation of timeutil.py.
// A time without an offset is refused: a verdict must not depend on the machine's time zone.
const RFC3339 = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/;

export class TimeError extends Error {}

export function parseRfc3339(text) {
  const m = RFC3339.exec(text);
  if (!m) throw new TimeError(`not an RFC 3339 timestamp with an explicit offset: ${text}`);
  const [year, month, day, hour, minute, second] = m.slice(1, 7).map(Number);
  const check = new Date(Date.UTC(year, month - 1, day, hour, minute, second));
  if (year < 1000 || check.getUTCFullYear() !== year || check.getUTCMonth() !== month - 1 || check.getUTCDate() !== day || hour > 23 || minute > 59 || second > 59) {
    throw new TimeError(`out-of-range timestamp: ${text}`);
  }
  let ms = check.getTime() + Number((m[7] ?? "0").padEnd(9, "0").slice(0, 3));
  const zone = m[8];
  if (zone !== "Z") {
    const sign = zone[0] === "+" ? 1 : -1;
    const hours = Number(zone.slice(1, 3));
    const minutes = Number(zone.slice(4, 6));
    if (hours > 23 || minutes > 59) throw new TimeError(`out-of-range offset: ${text}`);
    ms -= sign * (hours * 60 + minutes) * 60000;
  }
  // the instant, not only the written date, lies from the epoch to the end of the year 9999 (the range of the Python verifier's timeline)
  if (ms < 0 || ms >= Date.UTC(10000, 0, 1)) throw new TimeError(`out-of-range timestamp: ${text}`);
  return ms;
}

// YYYY-MM-DDTHH:MM:SS.mmmZ
export function formatMs(ms) {
  return new Date(ms).toISOString();
}
