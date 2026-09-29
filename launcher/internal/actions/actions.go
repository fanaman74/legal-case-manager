// Package actions parses and validates control requests against the
// compiled-in allow-list. Anything that is not exactly an allow-listed action
// with allow-listed parameters is rejected before it reaches Docker.
package actions

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

// MaxBody caps the request size; a valid request is well under 200 bytes.
const MaxBody = 1024

// Request is a validated action. Its fields only ever hold values taken from
// the catalog, never text copied from the request.
type Request struct {
	Action  catalog.ActionName
	Service *catalog.Service
	Enabled *bool
}

// Target is a short description for the audit log.
func (r Request) Target() string {
	switch {
	case r.Service != nil:
		return string(r.Service.ID)
	case r.Enabled != nil:
		return fmt.Sprintf("enabled=%t", *r.Enabled)
	default:
		return ""
	}
}

// ErrRejected wraps every validation failure.
var ErrRejected = errors.New("action rejected")

type wire struct {
	Action  *string `json:"action"`
	Service *string `json:"service"`
	Enabled *bool   `json:"enabled"`
}

func reject(format string, a ...any) error {
	return fmt.Errorf("%w: %s", ErrRejected, fmt.Sprintf(format, a...))
}

// Parse reads one JSON action request and validates it.
func Parse(r io.Reader) (Request, error) {
	body, err := io.ReadAll(io.LimitReader(r, MaxBody+1))
	if err != nil {
		return Request{}, reject("could not read request")
	}
	if len(body) > MaxBody {
		return Request{}, reject("request too large")
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	var w wire
	if err := dec.Decode(&w); err != nil {
		return Request{}, reject("malformed request")
	}
	if dec.More() {
		return Request{}, reject("trailing data")
	}
	var extra any
	if err := dec.Decode(&extra); err != io.EOF {
		return Request{}, reject("trailing data")
	}
	return validate(w)
}

func validate(w wire) (Request, error) {
	if w.Action == nil {
		return Request{}, reject("missing action")
	}
	spec, ok := catalog.LookupAction(*w.Action)
	if !ok {
		return Request{}, reject("unknown action")
	}
	req := Request{Action: spec.Name}

	if spec.NeedsService {
		if w.Service == nil {
			return Request{}, reject("missing service")
		}
		svc, ok := catalog.Lookup(*w.Service)
		if !ok {
			return Request{}, reject("unknown service")
		}
		req.Service = &svc
	} else if w.Service != nil {
		return Request{}, reject("unexpected service")
	}

	if spec.NeedsEnabled {
		if w.Enabled == nil {
			return Request{}, reject("missing enabled")
		}
		v := *w.Enabled
		req.Enabled = &v
	} else if w.Enabled != nil {
		return Request{}, reject("unexpected enabled")
	}
	return req, nil
}

// AllowedDuringSetup reports whether the setup-code holder may run r.
func (r Request) AllowedDuringSetup() bool {
	spec, _ := catalog.LookupAction(string(r.Action))
	return spec.AllowedDuringSetup
}
