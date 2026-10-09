package setup

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
)

func TestMergeConfigKeepsOtherSettings(t *testing.T) {
	l := newLayout(filepath.Join("C:", "CaseFiles"))
	o := Defaults()
	old := []byte("\xef\xbb\xbf" + `{"hostname":"casefiles.local","port":1,"data_dir":"elsewhere"}`)
	b, err := mergeConfig(old, l, o)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatal(err)
	}
	if got["hostname"] != "casefiles.local" || got["port"] != float64(8443) || got["data_dir"] != l.data || got["supervisor"] != "windows" {
		t.Fatalf("merged config = %v", got)
	}
	if _, err := mergeConfig([]byte("{broken"), l, o); err == nil {
		t.Fatal("a broken launcher.json should be reported")
	}
	if _, err := mergeConfig(nil, l, o); err != nil {
		t.Fatal(err)
	}
}

func TestACLPlanKeepsLauncherFilesPrivate(t *testing.T) {
	l := newLayout("/cf")
	rules := map[string]aclRule{}
	for _, r := range aclPlan(l) {
		rules[r.Path] = r
	}
	for _, p := range []string{l.state, l.certs} {
		r := rules[p]
		if !r.Exact || len(r.Grants) != 2 || strings.Contains(strings.Join(r.Grants, " "), "NT SERVICE") {
			t.Errorf("%s must be Administrators and SYSTEM only: %+v", p, r)
		}
	}
	data := strings.Join(rules[l.data].Grants, " ")
	if strings.Contains(data, "CaseFiles-models") || !strings.Contains(data, "CaseFiles-api") || !strings.Contains(data, "CaseFiles-worker") {
		t.Errorf("case data grants = %s", data)
	}
	if g := rules[filepath.Join(l.certs, "server.key")].Grants; len(g) != 1 || g[0] != `NT SERVICE\CaseFiles-api:R` {
		t.Errorf("server.key grants = %v", g)
	}
	if _, ok := rules[filepath.Join(l.certs, "ca.key")]; ok {
		t.Error("nobody may be granted the CA key")
	}
	// The install folder is reset before the private folders inside it.
	if aclPlan(l)[0].Path != l.root {
		t.Error("the install folder must be set first")
	}
}

func TestICACLSArgs(t *testing.T) {
	got := icaclsArgs(aclRule{Path: "p", Exact: true, Grants: []string{"a", "b"}})
	if len(got) != 2 || strings.Join(got[0], " ") != "p /reset /T /C /Q" || strings.Join(got[1], " ") != "p /inheritance:r /grant:r a b /C /Q" {
		t.Fatalf("exact = %v", got)
	}
	got = icaclsArgs(aclRule{Path: "p", Grants: []string{"a"}})
	if len(got) != 1 || strings.Join(got[0], " ") != "p /grant a /C /Q" {
		t.Fatalf("grant = %v", got)
	}
}

func TestFirewallRules(t *testing.T) {
	l := newLayout(`C:\Case Files`)
	rules := firewallRules(l, 443)
	if !strings.Contains(rules[0], `name="Case File Manager web app"`) || !strings.Contains(rules[0], "profile=private") ||
		!strings.Contains(rules[0], `program="`+l.python()+`"`) || !strings.Contains(rules[0], "localport=443") {
		t.Errorf("web app rule = %s", rules[0])
	}
	if !strings.Contains(rules[1], "dir=out action=block service=CaseFiles-worker") || strings.Contains(rules[1], "127.") {
		t.Errorf("worker rule = %s (loopback must stay reachable)", rules[1])
	}
	for _, d := range firewallDeletes() {
		if !strings.HasPrefix(d, `delete rule name="Case File Manager`) {
			t.Errorf("delete = %s", d)
		}
	}
}

func TestSetupCodeAndURL(t *testing.T) {
	text := "Case File Manager setup code\r\nABCD-EFGH-2345\r\nUse it once.\r\n"
	code := setupCode(text)
	if code != "ABCD-EFGH-2345" {
		t.Fatalf("code = %q", code)
	}
	if u := controlCenterURL(8443, code); u != "https://localhost:8443/#setup=ABCD-EFGH-2345" {
		t.Errorf("url = %s", u)
	}
	if u := controlCenterURL(8443, ""); u != "https://localhost:8443/" {
		t.Errorf("url = %s", u)
	}
	if setupCode("no code here") != "" {
		t.Error("no code expected")
	}
}
