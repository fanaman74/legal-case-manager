//! HTML email bodies to Markdown.
//!
//! Keeps what matters for reading and citing: paragraphs, headings, lists,
//! tables, quotes and links. Drops styles, scripts, images and layout. Never
//! fetches anything (remote images and links are text only). The tokenizer
//! is forgiving like a browser's: broken markup still gives its text.

use std::sync::LazyLock;

use regex::Regex;

use super::markdown::{escape, table, tidy};

const BLOCK: [&str; 16] =
    ["p", "div", "section", "article", "header", "footer", "main", "center", "address", "pre", "form", "fieldset", "dl", "dt", "dd", "hr"];
const SKIP: [&str; 11] = ["style", "script", "head", "title", "noscript", "template", "svg", "object", "iframe", "xml", "o:p"];
const VOID: [&str; 11] = ["br", "img", "hr", "meta", "link", "input", "wbr", "col", "area", "base", "source"];
/// Elements whose content is raw text, not markup.
const RAW_TEXT: [&str; 2] = ["script", "style"];

static LIST_ITEM: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^ +(- |\d+\. )").unwrap());
static ATTR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?"#).unwrap());

const QUOTE_OPEN: &str = "\x00QUOTE+\x00";
const QUOTE_CLOSE: &str = "\x00QUOTE-\x00";

fn is_heading(tag: &str) -> bool {
    tag.len() == 2 && tag.starts_with('h') && matches!(tag.as_bytes()[1], b'1'..=b'6')
}

#[derive(Default)]
struct TableState {
    rows: Vec<Vec<String>>,
    cell: Option<Vec<String>>,
}

#[derive(Default)]
struct Converter {
    out: Vec<String>,
    skip: usize,
    lists: Vec<(bool, usize)>, // (ordered, counter)
    link: Vec<Option<String>>,
    pre: usize,
    tables: Vec<TableState>,
    bold: usize,
    italic: usize,
}

impl Converter {
    /// Text goes to the current table cell if there is one, else the output.
    fn emit(&mut self, text: &str) {
        match self.tables.last_mut() {
            Some(t) => {
                if let Some(cell) = &mut t.cell {
                    cell.push(text.to_string());
                }
            }
            None => self.out.push(text.to_string()),
        }
    }

    fn brk(&mut self) {
        self.emit("\n\n");
    }

    fn start(&mut self, tag: &str, attrs: &[(String, String)]) {
        if SKIP.contains(&tag) {
            self.skip += 1;
            return;
        }
        if self.skip > 0 {
            return;
        }
        if BLOCK.contains(&tag) || is_heading(tag) {
            self.brk();
            if is_heading(tag) && self.tables.is_empty() {
                let n = (tag.as_bytes()[1] - b'0') as usize;
                self.emit(&format!("{} ", "#".repeat(n)));
            }
            if tag == "pre" {
                self.pre += 1;
            }
            if tag == "hr" {
                self.emit("---\n\n");
            }
            return;
        }
        match tag {
            "br" => self.emit(if self.tables.is_empty() { "  \n" } else { "\n" }),
            "ul" | "ol" => {
                if self.lists.is_empty() {
                    self.brk();
                }
                self.lists.push((tag == "ol", 0));
            }
            "li" => {
                self.emit("\n");
                let depth = self.lists.len().saturating_sub(1);
                let indent = "  ".repeat(depth);
                match self.lists.last_mut() {
                    Some((true, n)) => {
                        *n += 1;
                        let n = *n;
                        self.emit(&format!("{indent}{n}. "));
                    }
                    _ => self.emit(&format!("{indent}- ")),
                }
            }
            "blockquote" => {
                self.brk();
                self.emit(QUOTE_OPEN);
            }
            "a" => {
                let href = attrs.iter().find(|(k, _)| k == "href").map(|(_, v)| v.clone()).unwrap_or_default();
                let ok = ["http://", "https://", "mailto:"].iter().any(|p| href.starts_with(p));
                self.link.push(ok.then_some(href));
                if ok {
                    self.emit("[");
                }
            }
            "b" | "strong" => {
                self.bold += 1;
                if self.bold == 1 {
                    self.emit("**");
                }
            }
            "i" | "em" => {
                self.italic += 1;
                if self.italic == 1 {
                    self.emit("*");
                }
            }
            "table" => {
                self.brk();
                self.tables.push(TableState::default());
            }
            "tr" => {
                if let Some(t) = self.tables.last_mut() {
                    t.rows.push(Vec::new());
                }
            }
            "td" | "th" => {
                if let Some(t) = self.tables.last_mut() {
                    if t.rows.is_empty() {
                        t.rows.push(Vec::new());
                    }
                    t.cell = Some(Vec::new());
                }
            }
            _ => {}
        }
    }

    fn end(&mut self, tag: &str) {
        if SKIP.contains(&tag) {
            self.skip = self.skip.saturating_sub(1);
            return;
        }
        if self.skip > 0 {
            return;
        }
        if BLOCK.contains(&tag) || is_heading(tag) {
            if tag == "pre" {
                self.pre = self.pre.saturating_sub(1);
            }
            self.brk();
            return;
        }
        match tag {
            "ul" | "ol" => {
                self.lists.pop();
                if self.lists.is_empty() {
                    self.brk();
                }
            }
            "blockquote" => {
                self.emit(QUOTE_CLOSE);
                self.brk();
            }
            "a" => {
                if let Some(Some(href)) = self.link.pop() {
                    self.emit(&format!("]({})", href.replace(')', "%29").replace(' ', "%20")));
                }
            }
            "b" | "strong" => {
                self.bold = self.bold.saturating_sub(1);
                if self.bold == 0 {
                    self.emit("**");
                }
            }
            "i" | "em" => {
                self.italic = self.italic.saturating_sub(1);
                if self.italic == 0 {
                    self.emit("*");
                }
            }
            "td" | "th" => {
                if let Some(t) = self.tables.last_mut() {
                    if let Some(cell) = t.cell.take() {
                        let text = cell.concat().trim().to_string();
                        if let Some(row) = t.rows.last_mut() {
                            row.push(text);
                        }
                    }
                }
            }
            "table" => {
                if let Some(t) = self.tables.pop() {
                    let rows: Vec<Vec<String>> = t.rows.into_iter().filter(|r| !r.is_empty()).collect();
                    // Layout tables (one column, or one row) read better as text.
                    let rendered = if rows.len() > 1 && rows.iter().map(Vec::len).max().unwrap_or(0) > 1 {
                        format!("\n\n{}\n\n", table(&rows))
                    } else {
                        let cells: Vec<&String> = rows.iter().flatten().filter(|c| !c.is_empty()).collect();
                        format!("\n\n{}\n\n", cells.iter().map(|c| c.as_str()).collect::<Vec<_>>().join("\n\n"))
                    };
                    self.emit(&rendered);
                }
            }
            _ => {}
        }
    }

    fn data(&mut self, data: &str) {
        if self.skip > 0 {
            return;
        }
        if self.pre > 0 {
            self.emit(&escape(data));
            return;
        }
        let mut text = data.split_whitespace().collect::<Vec<_>>().join(" ");
        if text.is_empty() {
            if !data.is_empty() && self.out.last().is_some_and(|l| !l.ends_with([' ', '\n'])) {
                self.emit(" ");
            }
            return;
        }
        if data.starts_with(char::is_whitespace) {
            text.insert(0, ' ');
        }
        if data.ends_with(char::is_whitespace) {
            text.push(' ');
        }
        self.emit(&escape(&text));
    }
}

fn attributes(s: &str) -> Vec<(String, String)> {
    ATTR.captures_iter(s)
        .map(|c| {
            let v = c.get(2).or(c.get(3)).or(c.get(4)).map(|m| m.as_str()).unwrap_or("");
            (c[1].to_lowercase(), html_escape::decode_html_entities(v).into_owned())
        })
        .collect()
}

/// Feed the document to the converter, tag by tag.
fn tokenize(html: &str, conv: &mut Converter) {
    let mut rest = html;
    while !rest.is_empty() {
        let Some(lt) = rest.find('<') else {
            conv.data(&html_escape::decode_html_entities(rest));
            return;
        };
        if lt > 0 {
            conv.data(&html_escape::decode_html_entities(&rest[..lt]));
            rest = &rest[lt..];
        }
        // rest starts with '<'
        if rest.starts_with("<!--") {
            rest = rest.find("-->").map(|i| &rest[i + 3..]).unwrap_or("");
            continue;
        }
        if rest.starts_with("<!") || rest.starts_with("<?") {
            rest = rest.find('>').map(|i| &rest[i + 1..]).unwrap_or("");
            continue;
        }
        let closing = rest.starts_with("</");
        let name_start = if closing { 2 } else { 1 };
        let after = &rest[name_start..];
        if !after.starts_with(|c: char| c.is_ascii_alphabetic()) {
            // A '<' that doesn't start a tag is text.
            conv.data("<");
            rest = &rest[1..];
            continue;
        }
        let Some(gt) = find_tag_end(after) else {
            // Unclosed tag at the end: what came before is all there is.
            return;
        };
        let inner = &after[..gt];
        rest = &after[gt + 1..];
        let name_len = inner.find(|c: char| c.is_whitespace() || c == '/' || c == '>').unwrap_or(inner.len());
        let name = inner[..name_len].to_lowercase();
        if closing {
            conv.end(&name);
            continue;
        }
        let attrs_src = &inner[name_len..];
        let self_closing = attrs_src.trim_end().ends_with('/');
        let attrs = attributes(attrs_src.trim_end().trim_end_matches('/'));
        conv.start(&name, &attrs);
        if self_closing && !VOID.contains(&name.as_str()) {
            conv.end(&name);
        }
        if RAW_TEXT.contains(&name.as_str()) && !self_closing {
            // Everything up to the matching end tag is raw text.
            let lower = rest.to_ascii_lowercase();
            let close = format!("</{name}");
            match lower.find(&close) {
                Some(i) => {
                    conv.data(&rest[..i]);
                    rest = &rest[i..];
                }
                None => {
                    conv.data(rest);
                    rest = "";
                }
            }
        }
    }
}

/// The '>' that ends a tag, skipping '>' inside quoted attribute values.
fn find_tag_end(s: &str) -> Option<usize> {
    let mut quote: Option<char> = None;
    for (i, c) in s.char_indices() {
        match (quote, c) {
            (Some(q), c) if c == q => quote = None,
            (Some(_), _) => {}
            (None, '"' | '\'') => {
                // Only a quote that opens an attribute value counts.
                if s[..i].trim_end().ends_with('=') {
                    quote = Some(c);
                }
            }
            (None, '>') => return Some(i),
            _ => {}
        }
    }
    None
}

pub fn to_markdown(html: &str) -> String {
    let mut conv = Converter::default();
    tokenize(html, &mut conv);
    while !conv.tables.is_empty() {
        conv.end("table"); // unclosed tables
    }
    let raw = conv.out.concat();
    // Apply blockquote prefixes line by line.
    let mut lines: Vec<String> = Vec::new();
    let mut depth: usize = 0;
    for line in raw.split('\n') {
        let mut line = line.to_string();
        let mut line_depth = depth;
        while let Some(i) = line.find("\x00QUOTE") {
            let open = line.as_bytes()[i + 6] == b'+';
            depth = if open { depth + 1 } else { depth.saturating_sub(1) };
            line_depth = line_depth.max(depth);
            line.replace_range(i..i + 8, "");
        }
        let hard_break = line.ends_with("  ") && !line.trim().is_empty();
        let stripped = if LIST_ITEM.is_match(&line) { line.as_str() } else { line.trim_start_matches(' ') };
        let stripped = format!("{}{}", stripped.trim_end_matches(' '), if hard_break { "  " } else { "" });
        lines.push(if !stripped.is_empty() && line_depth > 0 {
            format!("{}{stripped}", "> ".repeat(line_depth))
        } else if depth > 0 {
            ">".to_string()
        } else {
            stripped
        });
    }
    tidy(&tidy_quotes(&lines).join("\n"))
}

fn only_marks(l: &str) -> bool {
    !l.is_empty() && l.chars().all(|c| c == '>')
}

/// Keep one bare ">" between quoted paragraphs and none at a quote's edges.
fn tidy_quotes(lines: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        if only_marks(line) {
            let next = lines[i + 1..].iter().find(|l| !only_marks(l)).map(String::as_str).unwrap_or("");
            if out.last().is_some_and(|p| p.starts_with('>') && !only_marks(p)) && next.starts_with('>') {
                out.push(line.clone());
            }
            continue;
        }
        out.push(line.clone());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::to_markdown;

    #[test]
    fn html_to_markdown() {
        let md = to_markdown("<style>x{}</style><h2>Terms</h2><p>See <a href='https://example.com'>here</a>.</p><ul><li>a<ul><li>b</li></ul></li></ul><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>");
        assert!(md.contains("## Terms"), "{md}");
        assert!(md.contains("[here](https://example.com)"), "{md}");
        assert!(md.contains("- a\n  - b"), "{md}");
        assert!(md.contains("| A | B |\n|---|---|\n| 1 | 2 |"), "{md}");
        assert!(!md.contains("x{}"));
        assert_eq!(to_markdown("<a href='javascript:alert(1)'>click</a>").trim(), "click");
    }

    #[test]
    fn quotes_and_entities() {
        let md = to_markdown("<p>Hi &amp; bye</p><blockquote><p>old</p><p>older</p></blockquote>");
        assert!(md.contains("Hi & bye"), "{md}");
        assert!(md.contains("> old\n>\n> older"), "{md}");
    }
}
