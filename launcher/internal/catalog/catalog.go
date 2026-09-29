// Package catalog is the fixed list of services and actions the launcher knows
// about. It is compiled in on purpose: nothing here can be extended from the
// browser, a config file, or the main app.
package catalog

// ServiceID identifies one managed service.
type ServiceID string

const (
	API    ServiceID = "api"
	Worker ServiceID = "worker"
	Models ServiceID = "models"
)

// Service describes a managed service.
type Service struct {
	ID ServiceID `json:"id"`
	// Name is shown to the Admin.
	Name string `json:"name"`
	// Purpose is one plain sentence about what the service does.
	Purpose string `json:"purpose"`
}

// Services lists every managed service in display order. OCR and PST parsing
// are libraries and programs the worker calls, not long-running services, so
// they appear as system checks instead.
var Services = []Service{
	{API, "Web app", "Serves the case manager to you and the people you invite."},
	{Worker, "Background worker", "Converts, chunks and indexes files, including OCR and PST parsing."},
	{Models, "Local AI models", "Runs the embedding model, and the chat model if you use a local one."},
}

// WindowsName is the Windows service that hosts s. Each one runs under its
// own virtual account (NT SERVICE\<name>), so file permissions and firewall
// rules can be set per service.
func (s Service) WindowsName() string { return "CaseFiles-" + string(s.ID) }

// Lookup returns the service with the given id.
func Lookup(id string) (Service, bool) {
	for _, s := range Services {
		if string(s.ID) == id {
			return s, true
		}
	}
	return Service{}, false
}

// ActionName is one allow-listed action.
type ActionName string

const (
	ServiceStart      ActionName = "service.start"
	ServiceStop       ActionName = "service.stop"
	ServiceRestart    ActionName = "service.restart"
	StackStartAll     ActionName = "stack.start_all"
	StackStopAll      ActionName = "stack.stop_all"
	DiagnosticsBundle ActionName = "diagnostics.bundle"
	CertRenew         ActionName = "cert.renew"
	SetLANBinding     ActionName = "launcher.set_lan_binding"
)

// ActionSpec says which parameters an action takes.
type ActionSpec struct {
	Name         ActionName
	NeedsService bool
	NeedsEnabled bool
	// AllowedDuringSetup marks actions the setup-code holder may run before an
	// Admin account exists (wizard steps 1 and 2).
	AllowedDuringSetup bool
}

// Actions is the complete allow-list.
var Actions = []ActionSpec{
	{Name: ServiceStart, NeedsService: true, AllowedDuringSetup: true},
	{Name: ServiceStop, NeedsService: true},
	{Name: ServiceRestart, NeedsService: true, AllowedDuringSetup: true},
	{Name: StackStartAll, AllowedDuringSetup: true},
	{Name: StackStopAll},
	{Name: DiagnosticsBundle},
	{Name: CertRenew},
	{Name: SetLANBinding, NeedsEnabled: true},
}

// LookupAction returns the spec for name.
func LookupAction(name string) (ActionSpec, bool) {
	for _, a := range Actions {
		if string(a.Name) == name {
			return a, true
		}
	}
	return ActionSpec{}, false
}
