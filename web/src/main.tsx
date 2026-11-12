import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { applyTheme, readTheme } from "./theme";
import "./styles.css";

applyTheme(readTheme());
createRoot(document.getElementById("root")!).render(<App />);
