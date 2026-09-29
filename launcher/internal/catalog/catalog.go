// Package catalog is the fixed list of services and actions the launcher knows
// about. It is compiled in on purpose: nothing here can be extended from the
// browser, a config file, or the main app.
package catalog

// ServiceID identifies one service in the compose project.
type ServiceID string

const (
	API    ServiceID = "api"
	Worker ServiceID = "worker"
	Queue  ServiceID = "queue"
	OCR    ServiceID = "ocr"
	PST    ServiceID = "pst"
	Models ServiceID = "models"
)

// Service describes a managed service.
type Service struct {
	ID ServiceID `json:"id"`
	// Name is shown to the Admin.
	Name string `json:"name"`
	// Purpose is one plain sentence about what the service does.
	Purpose string `json:"purpose"`
	// ComposeService is the service key in deploy/compose.yaml.
	ComposeService string `json:"-"`
}

// Services lists every managed service in display order.
var Services = []Service{
	{API, "Web app", "Serves the case manager to you and the people you invite.", "api"},
	{Worker, "Background worker", "Converts, chunks and indexes files in the background.", "worker"},
	{Queue, "Job queue", "Holds the list of background jobs waiting to run.", "queue"},
	{OCR, "OCR", "Reads text from scanned PDFs and images.", "ocr"},
	{PST, "PST parser", "Opens Outlook .pst archives.", "pst"},
	{Models, "Local AI models", "Runs the embedding model, and the chat model if you use a local one.", "models"},
}

// Lookup returns the service with the given id.
func Lookup(id string) (Service, bool) {
	for _, s := range Services {
		if string(s.ID) == id {
			return s, true
		}
	}
	return Service{}, false
}

// ByComposeService returns the service whose compose key matches name.
func ByComposeService(name string) (Service, bool) {
	for _, s := range Services {
		if s.ComposeService == name {
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
