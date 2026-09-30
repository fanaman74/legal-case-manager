package server

import (
	"crypto/rand"
	"encoding/hex"
	"sync"
	"time"

	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
)

// Operation is a service action running in the background.
type Operation struct {
	ID      string             `json:"id"`
	Action  catalog.ActionName `json:"action"`
	Service catalog.ServiceID  `json:"service,omitempty"`
	// Component is the component being installed right now, if any.
	Component  catalog.ComponentID `json:"component,omitempty"`
	Actor      string              `json:"actor"`
	State      string              `json:"state"` // running | succeeded | failed
	Message    string              `json:"message"`
	StartedAt  time.Time           `json:"startedAt"`
	FinishedAt *time.Time          `json:"finishedAt,omitempty"`
}

// Operation states.
const (
	OpRunning   = "running"
	OpSucceeded = "succeeded"
	OpFailed    = "failed"
)

type operations struct {
	mu  sync.Mutex
	ops []*Operation // newest last, capped
	// running is the one in-flight operation; operations are serialised so
	// two actions can't fight over the same services.
	running *Operation
}

// errBusy is returned when another action is still running.
type errBusy struct{ op Operation }

func (e errBusy) Error() string { return "another action is running" }

func newID() string {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func (o *operations) start(action catalog.ActionName, svc catalog.ServiceID, actor, msg string, now time.Time) (*Operation, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.running != nil {
		return nil, errBusy{*o.running}
	}
	op := &Operation{ID: newID(), Action: action, Service: svc, Actor: actor, State: OpRunning, Message: msg, StartedAt: now}
	o.running = op
	o.ops = append(o.ops, op)
	if len(o.ops) > 20 {
		o.ops = o.ops[len(o.ops)-20:]
	}
	return op, nil
}

func (o *operations) finish(op *Operation, ok bool, msg string, now time.Time) {
	o.mu.Lock()
	defer o.mu.Unlock()
	op.State = OpSucceeded
	if !ok {
		op.State = OpFailed
	}
	op.Message = msg
	op.FinishedAt = &now
	if o.running == op {
		o.running = nil
	}
}

// update changes a running operation's progress message.
func (o *operations) update(op *Operation, comp catalog.ComponentID, msg string) {
	o.mu.Lock()
	defer o.mu.Unlock()
	op.Component, op.Message = comp, msg
}

// current returns a copy of the running operation, if any.
func (o *operations) current() (Operation, bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.running == nil {
		return Operation{}, false
	}
	return *o.running, true
}

func (o *operations) list() []Operation {
	o.mu.Lock()
	defer o.mu.Unlock()
	out := make([]Operation, 0, len(o.ops))
	for i := len(o.ops) - 1; i >= 0; i-- {
		out = append(out, *o.ops[i])
	}
	return out
}

// busyServices maps services affected by the running operation to a label.
func (o *operations) busyServices() map[catalog.ServiceID]string {
	o.mu.Lock()
	defer o.mu.Unlock()
	out := map[catalog.ServiceID]string{}
	op := o.running
	if op == nil {
		return out
	}
	label := map[catalog.ActionName]string{
		catalog.ServiceStart:   "Starting",
		catalog.ServiceRestart: "Restarting",
		catalog.ServiceStop:    "Stopping",
		catalog.StackStartAll:  "Starting",
		catalog.StackStopAll:   "Stopping",
	}[op.Action]
	if label == "" {
		// Installs stop and start services themselves; their real state shows.
		return out
	}
	if op.Service != "" {
		out[op.Service] = label
		return out
	}
	for _, s := range catalog.Services {
		out[s.ID] = label
	}
	return out
}
