// Package redact removes secrets and case-file paths from log text before the
// launcher stores, shows or exports it.
package redact

import (
	"regexp"
	"strings"
)

// Each rule has cheap lower-case hints; the regular expression only runs when
// one of them appears in the line, which keeps busy logs fast.
var rules = []struct {
	hints []string
	re    *regexp.Regexp
	with  string
}{
	// Provider API keys (Anthropic, OpenAI, OpenRouter, DeepSeek style).
	{[]string{"sk-"}, regexp.MustCompile(`\bsk-[A-Za-z0-9_\-]{12,}`), "[redacted key]"},
	// Authorization headers.
	{[]string{"bearer", "basic"}, regexp.MustCompile(`(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/\-]+=*`), "$1 [redacted]"},
	// key=value and "key": "value" pairs with secret-sounding names.
	{[]string{"passw", "secret", "key", "token", "authorization", "cookie"}, regexp.MustCompile(`(?i)("?(?:password|passwd|secret|api[_\-]?key|token|authorization|cookie)"?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,;&]+)`), "$1[redacted]"},
	// Case files: names and paths can themselves be confidential. File names
	// may contain spaces, so everything up to a quote or the end of the line
	// is removed.
	{[]string{"cases"}, regexp.MustCompile(`(/data/cases/|[A-Za-z]:\\[^"'\n]*?\\data\\cases\\)[^"'\n]+`), "[case file]"},
}

// String returns s with secrets and case paths replaced.
func String(s string) string {
	low := strings.ToLower(s)
	for _, r := range rules {
		for _, h := range r.hints {
			if strings.Contains(low, h) {
				s = r.re.ReplaceAllString(s, r.with)
				low = strings.ToLower(s)
				break
			}
		}
	}
	return s
}
