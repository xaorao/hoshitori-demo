import type { Route } from "./route.ts";

// The pages are grouped by what a person is doing. The bar shows the four links marked primary and one menu, "All pages", that holds
// every page under its group; a phone shows the logo and the menu only.
export const GROUPS: {
  name: string;
  items: { route: Route; label: string; primary?: true }[];
}[] = [
  {
    name: "Discover",
    items: [
      { route: { page: "today" }, label: "Today", primary: true },
      { route: { page: "calls", cycle: null }, label: "Calls", primary: true },
      { route: { page: "feed" }, label: "Feed" },
      { route: { page: "voices" }, label: "Voices and whales" },
    ],
  },
  {
    name: "Investigate",
    items: [
      { route: { page: "claims" }, label: "Check a claim", primary: true },
      { route: { page: "desk" }, label: "The Desk" },
      { route: { page: "challenge" }, label: "Test a source" },
      { route: { page: "lab" }, label: "Laboratory" },
    ],
  },
  {
    name: "Results and verify",
    items: [
      { route: { page: "record" }, label: "Track record", primary: true },
      { route: { page: "verify" }, label: "Check a record" },
      { route: { page: "practice" }, label: "Practice trades" },
    ],
  },
  {
    name: "For reviewers",
    items: [
      { route: { page: "judges" }, label: "For judges" },
      { route: { page: "quality" }, label: "Evidence of quality" },
      { route: { page: "drill" }, label: "Failure drills" },
    ],
  },
];
