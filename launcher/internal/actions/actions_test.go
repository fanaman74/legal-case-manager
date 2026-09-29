package actions

import (
	"errors"
	"strings"
	"testing"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

func TestParseAcceptsEveryAllowListedShape(t *testing.T) {
	cases := map[string]catalog.ActionName{
		`{"action":"service.start","service":"api"}`:            catalog.ServiceStart,
		`{"action":"service.stop","service":"worker"}`:          catalog.ServiceStop,
		`{"action":"service.restart","service":"models"}`:       catalog.ServiceRestart,
		`{"action":"stack.start_all"}`:                          catalog.StackStartAll,
		`{"action":"stack.stop_all"}`:                           catalog.StackStopAll,
		`{"action":"diagnostics.bundle"}`:                       catalog.DiagnosticsBundle,
		`{"action":"cert.renew"}`:                               catalog.CertRenew,
		`{"action":"launcher.set_lan_binding","enabled":true}`:  catalog.SetLANBinding,
		`{"action":"launcher.set_lan_binding","enabled":false}`: catalog.SetLANBinding,
		"  {\"action\":\"stack.start_all\"}\n":                  catalog.StackStartAll,
	}
	for body, want := range cases {
		got, err := Parse(strings.NewReader(body))
		if err != nil {
			t.Errorf("%s: unexpected error %v", body, err)
			continue
		}
		if got.Action != want {
			t.Errorf("%s: got %s want %s", body, got.Action, want)
		}
	}
}

func TestParseServiceComesFromCatalog(t *testing.T) {
	got, err := Parse(strings.NewReader(`{"action":"service.restart","service":"models"}`))
	if err != nil {
		t.Fatal(err)
	}
	if got.Service == nil || got.Service.WindowsName() != "CaseFiles-models" || got.Service.ID != catalog.Models {
		t.Fatalf("service not resolved from catalog: %+v", got.Service)
	}
}

func TestParseRejects(t *testing.T) {
	bad := []string{
		``,
		`null`,
		`[]`,
		`{}`,
		`{"action":""}`,
		`{"action":"exec","service":"api"}`,
		`{"action":"service.exec","service":"api"}`,
		`{"action":"SERVICE.START","service":"api"}`,
		`{"action":"service.start"}`,
		`{"action":"service.start","service":""}`,
		`{"action":"service.start","service":"postgres"}`,
		`{"action":"service.start","service":"pst"}`,
		`{"action":"service.start","service":"CaseFiles-api"}`,
		`{"action":"service.start","service":"api; rm -rf /"}`,
		`{"action":"service.start","service":"../api"}`,
		`{"action":"service.start","service":"API"}`,
		`{"action":"service.start","service":["api"]}`,
		`{"action":"service.start","service":"api","command":"sh"}`,
		`{"action":"service.start","service":"api","enabled":true}`,
		`{"action":"stack.start_all","service":"api"}`,
		`{"action":"stack.start_all","image":"alpine"}`,
		`{"action":"launcher.set_lan_binding"}`,
		`{"action":"launcher.set_lan_binding","enabled":"yes"}`,
		`{"action":"launcher.set_lan_binding","enabled":true,"service":"api"}`,
		`{"action":"stack.start_all"}{"action":"stack.stop_all"}`,
		`{"action":"stack.start_all"} trailing`,
		`{"action":"stack.start_all","action":"exec"}`,
		`{"action":"stack.start_all","padding":"` + strings.Repeat("x", 2000) + `"}`,
	}
	for _, body := range bad {
		_, err := Parse(strings.NewReader(body))
		if !errors.Is(err, ErrRejected) {
			label := body
			if len(label) > 60 {
				label = label[:60] + "…"
			}
			t.Errorf("%q: expected rejection, got %v", label, err)
		}
	}
}

func TestSetupScope(t *testing.T) {
	allowed := map[catalog.ActionName]bool{
		catalog.StackStartAll:  true,
		catalog.ServiceStart:   true,
		catalog.ServiceRestart: true,
	}
	for _, a := range catalog.Actions {
		r := Request{Action: a.Name}
		if r.AllowedDuringSetup() != allowed[a.Name] {
			t.Errorf("%s: AllowedDuringSetup=%v", a.Name, r.AllowedDuringSetup())
		}
	}
}
