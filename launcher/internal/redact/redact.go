// Package redact removes secrets and case-file paths from log text before the
// launcher stores, shows or exports it.
package redact

import "regexp"

var rules = []struct {
	re   *regexp.Regexp
	with string
}{
	// Provider API keys (Anthropic, OpenAI, OpenRouter, DeepSeek style).
	{regexp.MustCompile(`\bsk-[A-Za-z0-9_\-]{12,}`), "[redacted key]"},
	// Authorization headers.
	{regexp.MustCompile(`(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/\-]+=*`), "$1 [redacted]"},
	// key=value and "key": "value" pairs with secret-sounding names.
	{regexp.MustCompile(`(?i)("?(?:password|passwd|secret|api[_\-]?key|token|authorization|cookie)"?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;&]+)`), "$1[redacted]"},
	// Case files: names and paths can themselves be confidential. File names
	// may contain spaces, so everything up to a quote or the end of the line
	// is removed.
	{regexp.MustCompile(`(/data/cases/|[A-Za-z]:\\CaseFiles\\data\\cases\\)[^"'\n]+`), "[case file]"},
}

// String returns s with secrets and case paths replaced.
func String(s string) string {
	for _, r := range rules {
		s = r.re.ReplaceAllString(s, r.with)
	}
	return s
}
