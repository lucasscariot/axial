// A deliberately small Markdown subset for text tiles: headings, paragraphs, lists,
// **bold**, *italic*, `code` and [links](https://…). Everything is HTML-escaped first.

const escape = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function inline(s: string): string {
  return escape(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

export function renderMarkdown(src: string): string {
  const out: string[] = [];
  let list: string[] | null = null;
  let para: string[] = [];
  const flush = () => {
    if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; }
    if (list) { out.push(`<ul>${list.map((i) => `<li>${inline(i)}</li>`).join("")}</ul>`); list = null; }
  };
  for (const raw of src.split("\n")) {
    const line = raw.trim();
    const h = line.match(/^(#{1,3})\s+(.*)$/);
    const li = line.match(/^[-*]\s+(.*)$/);
    if (!line) flush();
    else if (h) { flush(); out.push(`<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`); }
    else if (li) { if (para.length) flush(); (list ??= []).push(li[1]); }
    else { if (list) flush(); para.push(line); }
  }
  flush();
  return out.join("");
}

export function Markdown({ source }: { source: string }) {
  return <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(source) }} />;
}
