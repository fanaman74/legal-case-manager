//! Small Markdown helpers shared by the converters: escaping, tables, page
//! markers, paragraph clean-up and the YAML front matter. Same output as the
//! Python version's convert/markdown.py.

use std::sync::LazyLock;

use regex::Regex;
use serde_json::Value;

static INLINE_SPECIAL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"([\\`*_\[\]<>|])").unwrap());
static LINE_START: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(\s*)([#>+-]|\d+[.)])(\s)").unwrap());
static HYPHEN_BREAK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(\w)-\n(\w)").unwrap());
static BLANK_RUNS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n{3,}").unwrap());

/// Escape characters that would turn plain text into Markdown syntax.
pub fn escape(text: &str) -> String {
    let t = INLINE_SPECIAL.replace_all(text, r"\$1");
    // Python's re without MULTILINE: ^ is the start of the whole text.
    LINE_START.replacen(&t, 1, "$1\\$2$3").into_owned()
}

pub fn page_marker(n: usize) -> String {
    format!("<!-- page: {n} -->")
}

/// A GitHub-style table. The first row is the header. Cells keep their line
/// breaks as <br> so a row stays on one line.
pub fn table(rows: &[Vec<String>]) -> String {
    let rows: Vec<&Vec<String>> = rows.iter().filter(|r| r.iter().any(|c| !c.trim().is_empty())).collect();
    if rows.is_empty() {
        return String::new();
    }
    let width = rows.iter().map(|r| r.len()).max().unwrap();
    let cell = |c: &str| -> String {
        c.lines()
            .filter(|p| !p.trim().is_empty())
            .map(|p| p.split_whitespace().collect::<Vec<_>>().join(" "))
            .collect::<Vec<_>>()
            .join("<br>")
            .replace("\\|", "|")
            .replace('|', "\\|")
    };
    let mut lines = Vec::new();
    for (i, r) in rows.iter().enumerate() {
        let mut cells: Vec<String> = r.iter().map(|c| cell(c)).collect();
        cells.resize(width, String::new());
        lines.push(format!("| {} |", cells.join(" | ")));
        if i == 0 {
            lines.push(format!("|{}|", vec!["---"; width].join("|")));
        }
    }
    lines.join("\n")
}

/// Join the lines of one paragraph, mending words split with a hyphen at a
/// line end.
pub fn join_lines<S: AsRef<str>>(lines: &[S]) -> String {
    let text = lines.iter().map(|l| l.as_ref().trim()).filter(|l| !l.is_empty()).collect::<Vec<_>>().join("\n");
    let text = HYPHEN_BREAK.replace_all(&text, |c: &regex::Captures| {
        let second = &c[2];
        if second.chars().next().is_some_and(|ch| ch.is_lowercase()) {
            format!("{}{}", &c[1], second)
        } else {
            c[0].to_string()
        }
    });
    text.replace('\n', " ")
}

/// Collapse runs of blank lines and trailing spaces (keeping the two spaces
/// that mark a line break).
pub fn tidy(md: &str) -> String {
    let lines: Vec<String> = md
        .split('\n')
        .map(|line| {
            let keep = line.ends_with("  ") && !line.trim().is_empty();
            format!("{}{}", line.trim_end(), if keep { "  " } else { "" })
        })
        .collect();
    let md = BLANK_RUNS.replace_all(&lines.join("\n"), "\n\n").replace("  \n\n", "\n\n");
    format!("{}\n", md.trim())
}

fn yaml_str(v: &Value) -> String {
    // JSON strings and numbers are valid YAML scalars.
    match v {
        Value::String(_) | Value::Number(_) => serde_json::to_string(v).unwrap(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "null".into(),
        other => serde_json::to_string(&other.to_string()).unwrap(),
    }
}

/// YAML front matter, as an ordered list of (key, value). Every scalar is
/// written as a JSON string or number, so names with colons or quotes can't
/// break it.
pub fn front_matter(fields: &[(&str, Value)]) -> String {
    let mut lines = vec!["---".to_string()];
    for (key, value) in fields {
        match value {
            Value::Null => continue,
            Value::String(s) if s.is_empty() => continue,
            Value::Array(a) if a.is_empty() => continue,
            Value::Array(items) => {
                lines.push(format!("{key}:"));
                for v in items {
                    if let Value::Object(m) = v {
                        for (i, (k2, v2)) in m.iter().enumerate() {
                            lines.push(format!("  {}{k2}: {}", if i == 0 { "- " } else { "  " }, yaml_str(v2)));
                        }
                    } else {
                        lines.push(format!("  - {}", yaml_str(v)));
                    }
                }
            }
            Value::Bool(b) => lines.push(format!("{key}: {b}")),
            Value::Number(n) => lines.push(format!("{key}: {n}")),
            Value::String(s) => lines.push(format!("{key}: {}", serde_json::to_string(s).unwrap())),
            Value::Object(_) => lines.push(format!("{key}: {}", serde_json::to_string(&value.to_string()).unwrap())),
        }
    }
    lines.push("---".into());
    lines.join("\n") + "\n\n"
}

/// [1, 2, 3, 7] -> "1-3, 7"
pub fn page_list(pages: &[usize]) -> String {
    let mut sorted = pages.to_vec();
    sorted.sort();
    let mut out = Vec::new();
    let mut i = 0;
    while i < sorted.len() {
        let start = sorted[i];
        let mut end = start;
        while i + 1 < sorted.len() && sorted[i + 1] == end + 1 {
            i += 1;
            end = sorted[i];
        }
        out.push(if start == end { start.to_string() } else { format!("{start}-{end}") });
        i += 1;
    }
    out.join(", ")
}

/// A file name from an email attachment: no folders, no control characters,
/// not too long.
pub fn safe_name(name: &str, default: &str) -> String {
    let last = name.rsplit(['\\', '/']).next().unwrap_or("");
    let cleaned: String = last.chars().filter(|c| (*c as u32) >= 32 && !"<>:\"|?*".contains(*c)).collect();
    let mut name = cleaned.trim_matches([' ', '.']).to_string();
    if name.is_empty() {
        name = default.to_string();
    }
    if name.chars().count() > 150 {
        let take = |s: &str, n: usize| s.chars().take(n).collect::<String>();
        name = match name.rsplit_once('.') {
            Some((stem, ext)) if ext.chars().count() <= 10 => format!("{}.{ext}", take(stem, 150 - ext.chars().count() - 1)),
            _ => take(&name, 150),
        };
    }
    name
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn escaping() {
        assert_eq!(escape("# not a heading"), "\\# not a heading");
        assert_eq!(escape("a*b_c"), "a\\*b\\_c");
        assert_eq!(escape("1. item"), "\\1. item");
    }

    #[test]
    fn tables_like_python() {
        let rows = vec![vec!["a|b".to_string(), "c\nd".to_string()], vec!["1".to_string(), String::new()]];
        assert_eq!(table(&rows), "| a\\|b | c<br>d |\n|---|---|\n| 1 |  |");
    }

    #[test]
    fn front_matter_like_python() {
        let fm = front_matter(&[
            ("doc_id", json!("abc")),
            ("source_filename", json!("He said: \"no\" #1")),
            ("page_count", json!(3)),
            ("ocr", json!(false)),
            ("to", json!(["a <a@x>", "b"])),
            ("empty", Value::Null),
            ("attachments", json!([{"name": "x.pdf", "doc_id": "1"}])),
        ]);
        assert!(fm.contains("source_filename: \"He said: \\\"no\\\" #1\""));
        assert!(fm.contains("page_count: 3\nocr: false"));
        assert!(fm.contains("  - \"a <a@x>\""));
        assert!(fm.contains("  - name: \"x.pdf\"\n    doc_id: \"1\""));
        assert!(!fm.contains("empty"));
    }

    #[test]
    fn helpers() {
        assert_eq!(page_list(&[1, 2, 3, 7]), "1-3, 7");
        assert_eq!(join_lines(&["claim-", "ant said", "Smith-", "Jones"]), "claimant said Smith- Jones");
        assert_eq!(tidy("a  \n\n\n\nb   \nc  "), "a\n\nb  \nc\n");
        assert_eq!(safe_name("../x/evil?.pdf", "attachment"), "evil.pdf");
        assert_eq!(safe_name("", "attachment"), "attachment");
    }
}
