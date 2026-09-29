package compose

import (
	"reflect"
	"strings"
	"testing"

	"github.com/fanaman74/legal-case-manager/launcher/internal/actions"
	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

func parse(t *testing.T, body string) actions.Request {
	t.Helper()
	r, err := actions.Parse(strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func TestArgs(t *testing.T) {
	p := Project{File: "/opt/cfm/deploy/compose.yaml", Name: "casefiles"}
	base := []string{"compose", "--project-name", "casefiles", "--file", "/opt/cfm/deploy/compose.yaml"}
	cases := []struct {
		body string
		tail []string
	}{
		{`{"action":"stack.start_all"}`, []string{"up", "--detach", "--wait", "--wait-timeout", "600"}},
		{`{"action":"stack.stop_all"}`, []string{"stop"}},
		{`{"action":"service.start","service":"worker"}`, []string{"up", "--detach", "--no-deps", "worker"}},
		{`{"action":"service.stop","service":"ocr"}`, []string{"stop", "ocr"}},
		{`{"action":"service.restart","service":"models"}`, []string{"up", "--detach", "--no-deps", "--force-recreate", "models"}},
	}
	for _, c := range cases {
		got, ok := p.Args(parse(t, c.body))
		if !ok {
			t.Fatalf("%s: not a compose action", c.body)
		}
		want := append(append([]string{}, base...), c.tail...)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("%s:\n got %q\nwant %q", c.body, got, want)
		}
	}
}

func TestNonComposeActionsHaveNoArgs(t *testing.T) {
	p := Project{File: "f", Name: "n"}
	for _, body := range []string{`{"action":"cert.renew"}`, `{"action":"diagnostics.bundle"}`, `{"action":"launcher.set_lan_binding","enabled":true}`} {
		if _, ok := p.Args(parse(t, body)); ok {
			t.Errorf("%s should not map to compose", body)
		}
	}
}

// Every service argument must be a catalog compose key: no request text can
// reach the command line.
func TestArgsOnlyContainCatalogValues(t *testing.T) {
	p := Project{File: "F", Name: "N"}
	fixed := map[string]bool{"compose": true, "--project-name": true, "N": true, "--file": true, "F": true,
		"up": true, "--detach": true, "--wait": true, "--wait-timeout": true, "600": true, "stop": true,
		"--no-deps": true, "--force-recreate": true}
	for _, s := range catalog.Services {
		fixed[s.ComposeService] = true
	}
	for _, a := range catalog.Actions {
		for _, s := range catalog.Services {
			svc := s
			r := actions.Request{Action: a.Name}
			if a.NeedsService {
				r.Service = &svc
			}
			args, ok := p.Args(r)
			if !ok {
				continue
			}
			for _, arg := range args {
				if !fixed[arg] {
					t.Errorf("%s/%s: unexpected argument %q", a.Name, s.ID, arg)
				}
			}
		}
	}
}
