import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Site } from "./site.tsx";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("the page has no #root");
createRoot(root).render(
  <StrictMode>
    <Site />
  </StrictMode>,
);
