package redact

import (
	"strings"
	"testing"
)

func TestString(t *testing.T) {
	cases := map[string][]string{
		"using key sk-ant-api03-abcdefghijklmnopqrstuvwx for anthropic":          {"sk-ant-api03"},
		"Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig":                 {"eyJhbGci"},
		`{"api_key": "abc123secret", "model": "x"}`:                              {"abc123secret"},
		"password=hunter2hunter2 user=fred":                                      {"hunter2"},
		"converting /data/cases/case_42/original/Smith privileged memo.docx now": {"case_42", "privileged"},
		`opened C:\CaseFiles\data\cases\case_42\original\memo.pdf`:               {"case_42", "memo.pdf"},
		`opened D:\Legal\CaseFiles\data\cases\case_7\original\Jones letter.pdf`:  {"case_7", "Jones"},
	}
	for in, secrets := range cases {
		out := String(in)
		for _, s := range secrets {
			if strings.Contains(out, s) {
				t.Errorf("%q still contains %q: %q", in, s, out)
			}
		}
	}
	if got := String("worker started, 3 jobs queued"); got != "worker started, 3 jobs queued" {
		t.Errorf("ordinary text changed: %q", got)
	}
	if got := String(`"model": "bge-m3"`); !strings.Contains(got, "bge-m3") {
		t.Errorf("non-secret field changed: %q", got)
	}
}
