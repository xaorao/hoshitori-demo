// The address of a page, from the part of the URL after "#". Pure, so it can be tested.

export type Route =
  | { page: "today" }
  | { page: "calls"; cycle: string | null }
  | { page: "callsby"; by: "token" | "agent"; value: string } // every call on one token, or of one agent
  | { page: "record" }
  | { page: "verify" }
  | { page: "quality" }
  | { page: "drill" }
  | { page: "brand" }
  | { page: "claims" }
  | { page: "challenge" }
  | { page: "desk"; run?: string } // one run opened first, when a receipt links to it
  | { page: "practice"; book?: string } // one book opened first
  | { page: "lab" }
  | { page: "judges" }
  | { page: "feed" }
  | { page: "voices" }
  | { page: "receipt"; id: string }
  | { page: "missing"; path: string };

const RECEIPT = /^\/receipts\/(fc[a-z2-7]{26,})$/;
const CALLS = /^\/calls(?:\/(cy[a-z2-7]{26,}))?$/;
const CALLS_BY = /^\/calls\/(token|agent)\/([A-Za-z0-9]{1,20})$/;
const NAMED = /^\/(desk|practice)\/([A-Za-z0-9][A-Za-z0-9._:-]{0,127})$/; // a run id or a book name: the characters the records allow

export function parse(hash: string): Route {
  const path = hash.replace(/^#/, "") || "/";
  if (path === "/") return { page: "today" };
  const calls = CALLS.exec(path);
  if (calls !== null) return { page: "calls", cycle: calls[1] ?? null };
  const by = CALLS_BY.exec(path);
  if (by !== null && by[2] !== undefined)
    return { page: "callsby", by: by[1] === "agent" ? "agent" : "token", value: by[2] };
  if (path === "/record") return { page: "record" };
  if (path === "/verify") return { page: "verify" };
  if (path === "/quality") return { page: "quality" };
  if (path === "/drill") return { page: "drill" };
  if (path === "/brand") return { page: "brand" };
  if (path === "/claims") return { page: "claims" };
  if (path === "/challenge") return { page: "challenge" };
  if (path === "/desk") return { page: "desk" };
  if (path === "/practice") return { page: "practice" };
  if (path === "/lab") return { page: "lab" };
  if (path === "/judges") return { page: "judges" };
  if (path === "/feed") return { page: "feed" };
  if (path === "/voices") return { page: "voices" };
  const named = NAMED.exec(path);
  if (named !== null && named[2] !== undefined)
    return named[1] === "desk"
      ? { page: "desk", run: named[2] }
      : { page: "practice", book: named[2] };
  const m = RECEIPT.exec(path);
  if (m !== null && m[1] !== undefined) return { page: "receipt", id: m[1] };
  return { page: "missing", path };
}

export function href(route: Route): string {
  switch (route.page) {
    case "today":
      return "#/";
    case "calls":
      return route.cycle === null ? "#/calls" : `#/calls/${route.cycle}`;
    case "callsby":
      return `#/calls/${route.by}/${route.value}`;
    case "record":
      return "#/record";
    case "verify":
      return "#/verify";
    case "quality":
      return "#/quality";
    case "drill":
      return "#/drill";
    case "brand":
      return "#/brand";
    case "claims":
      return "#/claims";
    case "challenge":
      return "#/challenge";
    case "desk":
      return route.run === undefined ? "#/desk" : `#/desk/${route.run}`;
    case "practice":
      return route.book === undefined ? "#/practice" : `#/practice/${route.book}`;
    case "lab":
      return "#/lab";
    case "judges":
      return "#/judges";
    case "feed":
      return "#/feed";
    case "voices":
      return "#/voices";
    case "receipt":
      return `#/receipts/${route.id}`;
    case "missing":
      return "#/";
  }
}
