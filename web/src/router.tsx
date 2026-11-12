import { useEffect, useState, type MouseEvent, type ReactNode } from "react";

export function navigate(to: string) {
  window.history.pushState({}, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function useLocation() {
  const [loc, setLoc] = useState({ path: window.location.pathname, search: window.location.search });
  useEffect(() => {
    const on = () => setLoc({ path: window.location.pathname, search: window.location.search });
    window.addEventListener("popstate", on);
    return () => window.removeEventListener("popstate", on);
  }, []);
  return loc;
}

export function setSearch(params: Record<string, string | null>) {
  const u = new URL(window.location.href);
  for (const [k, v] of Object.entries(params)) v === null ? u.searchParams.delete(k) : u.searchParams.set(k, v);
  window.history.replaceState({}, "", u.pathname + u.search);
}

export function Link({ to, children, className, title }: { to: string; children: ReactNode; className?: string; title?: string }) {
  const onClick = (e: MouseEvent) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
  };
  return <a href={to} onClick={onClick} className={className} title={title}>{children}</a>;
}
