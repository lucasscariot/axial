// Small 16px stroke icons, drawn inline so the app needs no icon dependency.
import type { SVGProps } from "react";

const base = (p: SVGProps<SVGSVGElement>) => ({
  width: 16, height: 16, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor",
  strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true, ...p,
});

export const IconInspect = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M6.5 3.5h-3v9h9v-3" /><path d="M9 2.5h4.5V7" /><path d="M13.5 2.5 7.5 8.5" /></svg>
);
export const IconReload = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" /><path d="M13.5 2.5v3h-3" /></svg>
);
export const IconDownload = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M8 2.5v7.5" /><path d="m4.75 7 3.25 3.25L11.25 7" /><path d="M2.75 13.25h10.5" /></svg>
);
export const IconSun = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><circle cx="8" cy="8" r="2.75" /><path d="M8 1.5v1.25M8 13.25v1.25M1.5 8h1.25M13.25 8h1.25M3.4 3.4l.9.9M11.7 11.7l.9.9M3.4 12.6l.9-.9M11.7 4.3l.9-.9" /></svg>
);
export const IconMoon = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M13.5 9.6A5.75 5.75 0 0 1 6.4 2.5a5.75 5.75 0 1 0 7.1 7.1Z" /></svg>
);
export const IconMonitor = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><rect x="1.75" y="2.5" width="12.5" height="8.5" rx="1.5" /><path d="M5.5 14h5M8 11v3" /></svg>
);
export const IconX = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="m4 4 8 8M12 4l-8 8" /></svg>
);
export const IconArrowUpRight = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><path d="M5 11 11 5M6 5h5v5" /></svg>
);
export const IconInfo = (p: SVGProps<SVGSVGElement>) => (
  <svg {...base(p)}><circle cx="8" cy="8" r="6.25" /><path d="M8 7.25v3.5M8 5.25h.01" /></svg>
);
export const IconSort = ({ dir, ...p }: SVGProps<SVGSVGElement> & { dir: 1 | -1 | 0 }) => (
  <svg {...base({ width: 12, height: 12, ...p })}>
    {dir >= 0 && <path d="m5 6.5 3-3 3 3" opacity={dir === 1 ? 1 : 0.35} />}
    {dir <= 0 && <path d="m5 9.5 3 3 3-3" opacity={dir === -1 ? 1 : 0.35} />}
  </svg>
);
