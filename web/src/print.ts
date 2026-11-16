// PDF export. The browser's print dialog turns the dashboard into a PDF ("Save as PDF").
// Before it opens, print mode lays the page out at the printable width of an A4 landscape
// sheet and forces the light theme, so charts redraw for paper rather than being scaled.

/** Printable width of A4 landscape with 12 mm margins (see `@page` in styles.css), in CSS px. */
export const PRINT_WIDTH = 1030;

const frame = () => new Promise<void>((r) => requestAnimationFrame(() => r()));

/** Apply print mode to the document. Returns a function that restores the previous state. */
export function enterPrintMode(title: string): () => void {
  const root = document.documentElement;
  const prevTheme = root.getAttribute("data-theme");
  const prevTitle = document.title;
  root.setAttribute("data-printing", "true");
  root.setAttribute("data-theme", "light"); // not persisted: the viewer's preference stays
  document.title = title; // Chrome and Safari use it as the default PDF file name
  window.dispatchEvent(new Event("axial:theme"));
  return () => {
    root.removeAttribute("data-printing");
    if (prevTheme === null) root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", prevTheme);
    document.title = prevTitle;
    window.dispatchEvent(new Event("axial:theme"));
  };
}

/** Wait for fonts, the resize observers and the charts' next paint after a layout change. */
export async function settle() {
  await document.fonts?.ready;
  await frame();
  await frame();
  await new Promise((r) => setTimeout(r, 150));
  await frame();
}

/** File-name-safe title such as "Booking operations 2026-11-05 to 2026-11-11". */
export function pdfTitle(title: string, start?: string, endInclusive?: string) {
  const range = start && endInclusive ? (start === endInclusive ? ` ${start}` : ` ${start} to ${endInclusive}`) : "";
  return (title + range).replace(/[\\/:*?"<>|]+/g, "-");
}
