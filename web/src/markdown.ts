// Renders converted Markdown for reading. Raw HTML in the Markdown is shown
// as text, never run, and links open in a new tab without a referrer: the
// Markdown comes from uploaded files, so it is treated as untrusted.
import MarkdownIt from "markdown-it";
import footnote from "markdown-it-footnote";

const md = new MarkdownIt({ html: false, linkify: false, typographer: false }).use(footnote);
const defaultLink = md.renderer.rules.link_open ?? ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return defaultLink(tokens, idx, options, env, self);
};

export function renderMarkdown(text: string): string {
  return md.render(text);
}

export type PageSection = { page: number | null; text: string };

const MARKER = /^<!-- page: (\d+) -->$/gm;

/** Splits a converted document at its page markers. Text before the first
 * marker (an email's header, say) has page null. */
export function splitPages(body: string): PageSection[] {
  const out: PageSection[] = [];
  let last = 0;
  let page: number | null = null;
  for (const m of body.matchAll(MARKER)) {
    const text = body.slice(last, m.index).trim();
    if (text || page !== null) out.push({ page, text });
    page = Number(m[1]);
    last = (m.index ?? 0) + m[0].length;
  }
  const rest = body.slice(last).trim();
  if (rest || page !== null) out.push({ page, text: rest });
  return out;
}
