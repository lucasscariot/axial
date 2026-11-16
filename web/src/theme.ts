export type ThemePref = "system" | "light" | "dark";

const KEY = "axial.theme";

export function readTheme(): ThemePref {
  try {
    let v = localStorage.getItem(KEY);
    if (v === null) {
      v = localStorage.getItem("bqdash.theme");
      if (v === "light" || v === "dark") localStorage.setItem(KEY, v);
    }
    localStorage.removeItem("bqdash.theme");
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(pref: ThemePref) {
  const root = document.documentElement;
  if (pref === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch { /* storage unavailable: theme still applies for this visit */ }
  window.dispatchEvent(new Event("axial:theme"));
}
