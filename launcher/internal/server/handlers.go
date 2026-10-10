package server

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"mime"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"rsc.io/qr"

	"github.com/fanaman74/legal-case-manager/launcher/internal/actions"
	"github.com/fanaman74/legal-case-manager/launcher/internal/audit"
	"github.com/fanaman74/legal-case-manager/launcher/internal/auth"
	"github.com/fanaman74/legal-case-manager/launcher/internal/catalog"
	"github.com/fanaman74/legal-case-manager/launcher/internal/config"
	"github.com/fanaman74/legal-case-manager/launcher/internal/netguard"
	"github.com/fanaman74/legal-case-manager/launcher/internal/procs"
	"github.com/fanaman74/legal-case-manager/launcher/internal/redact"
	"github.com/fanaman74/legal-case-manager/launcher/internal/status"
)

const (
	sessionCookie = "cc_session"
	csrfHeader    = "X-CSRF-Token"
)

type ctxKey int

const sessionKey ctxKey = 0

func (s *Server) routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/session", s.handleSession)
	mux.HandleFunc("POST /api/session/setup-code", s.public(s.handleSetupCode))
	mux.HandleFunc("POST /api/session/login", s.public(s.handleLogin))
	mux.HandleFunc("POST /api/session/admin", s.require(auth.ScopeSetup, s.handleCreateAdmin))
	mux.HandleFunc("POST /api/session/logout", s.require("", s.handleLogout))
	mux.HandleFunc("GET /api/status", s.require("", s.handleStatus))
	mux.HandleFunc("GET /api/events", s.require("", s.handleEvents))
	mux.HandleFunc("POST /api/actions", s.require("", s.handleAction))
	mux.HandleFunc("GET /api/logs", s.require(auth.ScopeAdmin, s.handleLogs))
	mux.HandleFunc("GET /api/audit", s.require(auth.ScopeAdmin, s.handleAudit))
	mux.HandleFunc("GET /api/lan-qr", s.require("", s.handleQR))
	mux.HandleFunc("GET /api/ca-certificate", s.require("", s.handleCACert))
	mux.HandleFunc("GET /", s.handleStatic)
	return s.guard(securityHeaders(mux))
}

// guard rejects clients outside the allowed network before anything else.
func (s *Server) guard(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !netguard.Allowed(netguard.ClientIP(r), s.cfg.LoadState().LANEnabled) {
			http.Error(w, "The Control Center only accepts connections from this computer.", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Cross-Origin-Opener-Policy", "same-origin")
		h.Set("Cross-Origin-Resource-Policy", "same-origin")
		if strings.HasPrefix(r.URL.Path, "/api/") {
			h.Set("Cache-Control", "no-store")
		}
		next.ServeHTTP(w, r)
	})
}

type apiError struct {
	Error string `json:"error"`
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, code int, msg string) {
	writeJSON(w, code, apiError{msg})
}

// sameOrigin is the CSRF backstop for every state-changing request: the
// browser's Origin must be this server, and the body must be JSON (which a
// cross-site form cannot send without a preflight).
func sameOrigin(r *http.Request) bool {
	if o := r.Header.Get("Origin"); o != "" && o != "https://"+r.Host {
		return false
	}
	if site := r.Header.Get("Sec-Fetch-Site"); site != "" && site != "same-origin" {
		return false
	}
	mt, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	return mt == "application/json"
}

func (s *Server) session(r *http.Request) (*auth.Session, bool) {
	c, err := r.Cookie(sessionCookie)
	if err != nil {
		return nil, false
	}
	return s.auth.Lookup(c.Value)
}

// public wraps unauthenticated POSTs (setup code, login).
func (s *Server) public(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if !sameOrigin(r) {
			writeError(w, http.StatusForbidden, "This request didn't come from the Control Center page. Reload the page and try again.")
			return
		}
		h(w, r)
	}
}

// require checks the session scope. An empty scope accepts setup or admin.
// Non-GET requests also need the session's CSRF token.
func (s *Server) require(scope auth.Scope, h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		sess, ok := s.session(r)
		if !ok {
			writeError(w, http.StatusUnauthorized, "Your session has ended. Sign in again.")
			return
		}
		if scope != "" && sess.Scope != scope {
			writeError(w, http.StatusForbidden, "Only the Admin can do this.")
			return
		}
		if r.Method != http.MethodGet {
			if !sameOrigin(r) || r.Header.Get(csrfHeader) != sess.CSRF {
				writeError(w, http.StatusForbidden, "This request didn't come from the Control Center page. Reload the page and try again.")
				return
			}
		}
		h(w, r.WithContext(context.WithValue(r.Context(), sessionKey, sess)))
	}
}

func sessionFrom(r *http.Request) *auth.Session {
	sess, _ := r.Context().Value(sessionKey).(*auth.Session)
	return sess
}

func setSessionCookie(w http.ResponseWriter, sess *auth.Session) {
	http.SetCookie(w, &http.Cookie{
		Name: sessionCookie, Value: sess.Token, Path: "/",
		HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode,
	})
}

func clearSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name: sessionCookie, Value: "", Path: "/", MaxAge: -1,
		HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode,
	})
}

type sessionView struct {
	State string `json:"state"` // needs_setup | signed_out | setup | admin
	User  string `json:"user,omitempty"`
	CSRF  string `json:"csrf,omitempty"`
}

func (s *Server) handleSession(w http.ResponseWriter, r *http.Request) {
	if sess, ok := s.session(r); ok {
		writeJSON(w, http.StatusOK, sessionView{State: string(sess.Scope), User: sess.User, CSRF: sess.CSRF})
		return
	}
	if s.auth.HasAdmin() {
		writeJSON(w, http.StatusOK, sessionView{State: "signed_out"})
		return
	}
	writeJSON(w, http.StatusOK, sessionView{State: "needs_setup"})
}

func decodeBody(r *http.Request, v any) error {
	dec := json.NewDecoder(io.LimitReader(r.Body, 4096))
	dec.DisallowUnknownFields()
	return dec.Decode(v)
}

func (s *Server) record(r *http.Request, actor, action, target, outcome, detail string) error {
	return s.audit.Append(audit.Entry{
		Actor: actor, IP: netguard.ClientIP(r).String(), Action: action,
		Target: target, Outcome: outcome, Detail: detail,
	})
}

func (s *Server) handleSetupCode(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Code string `json:"code"`
	}
	if err := decodeBody(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "Enter the setup code.")
		return
	}
	sess, err := s.auth.RedeemSetupCode(body.Code, netguard.ClientIP(r).String())
	switch {
	case errors.Is(err, auth.ErrNoSetup):
		writeError(w, http.StatusConflict, "Setup is already complete. Sign in with the Admin account.")
		return
	case errors.Is(err, auth.ErrLockedOut):
		_ = s.record(r, "anonymous", "auth.setup_code", "", audit.Denied, "locked out")
		writeError(w, http.StatusTooManyRequests, "Too many wrong codes. Wait 15 minutes, then try again.")
		return
	case err != nil:
		_ = s.record(r, "anonymous", "auth.setup_code", "", audit.Denied, "wrong code")
		writeError(w, http.StatusUnauthorized, "That code doesn't match. Open setup-code.txt in the launcher folder (C:\\CaseFiles\\launcher on a standard install) and copy the code exactly.")
		return
	}
	_ = s.record(r, "setup", "auth.setup_code", "", audit.Succeeded, "")
	setSessionCookie(w, sess)
	writeJSON(w, http.StatusOK, sessionView{State: string(sess.Scope), User: sess.User, CSRF: sess.CSRF})
}

func (s *Server) handleCreateAdmin(w http.ResponseWriter, r *http.Request) {
	sess := sessionFrom(r)
	var body struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := decodeBody(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "Enter a username and password.")
		return
	}
	if err := s.record(r, "setup", "auth.create_admin", body.Username, audit.Requested, ""); err != nil {
		writeError(w, http.StatusServiceUnavailable, auditDownMsg)
		return
	}
	err := s.auth.CreateAdmin(sess, body.Username, body.Password)
	switch {
	case errors.Is(err, auth.ErrBadUsername):
		writeError(w, http.StatusBadRequest, "Use 2 to 64 letters, numbers, dots, dashes, underscores or @ for the username.")
		return
	case errors.Is(err, auth.ErrWeakPassword):
		writeError(w, http.StatusBadRequest, fmt.Sprintf("Use at least %d characters for the password. A short sentence works well.", auth.MinPasswordLen))
		return
	case errors.Is(err, auth.ErrNoSetup):
		writeError(w, http.StatusConflict, "The Admin account already exists. Sign in with it.")
		return
	case err != nil:
		s.log.Error("create admin", "err", err)
		writeError(w, http.StatusInternalServerError, "The account couldn't be saved. Check that the data folder isn't read-only, then try again.")
		return
	}
	_ = s.record(r, sess.User, "auth.create_admin", sess.User, audit.Succeeded, "")
	if err := s.provisionAppAdmin(); err != nil {
		s.log.Error("hand the Admin account to the app", "err", err)
	}
	s.poller.refresh(r.Context())
	writeJSON(w, http.StatusOK, sessionView{State: string(sess.Scope), User: sess.User, CSRF: sess.CSRF})
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := decodeBody(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "Enter your username and password.")
		return
	}
	sess, err := s.auth.Login(body.Username, body.Password, netguard.ClientIP(r).String())
	switch {
	case errors.Is(err, auth.ErrLockedOut):
		_ = s.record(r, body.Username, "auth.login", "", audit.Denied, "locked out")
		writeError(w, http.StatusTooManyRequests, "Too many failed attempts. Wait 15 minutes, then try again.")
		return
	case err != nil:
		_ = s.record(r, body.Username, "auth.login", "", audit.Denied, "wrong username or password")
		writeError(w, http.StatusUnauthorized, "That username and password don't match. Check Caps Lock and try again.")
		return
	}
	_ = s.record(r, sess.User, "auth.login", "", audit.Succeeded, "")
	setSessionCookie(w, sess)
	writeJSON(w, http.StatusOK, sessionView{State: string(sess.Scope), User: sess.User, CSRF: sess.CSRF})
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	sess := sessionFrom(r)
	s.auth.Logout(sess.Token)
	_ = s.record(r, sess.User, "auth.logout", "", audit.Succeeded, "")
	clearSessionCookie(w)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.poller.get())
}

// handleEvents streams snapshots as server-sent events.
func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	fl, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "Live updates aren't supported here.")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")
	ch, cancel := s.poller.subscribe()
	defer cancel()
	token := sessionFrom(r).Token
	send := func(snap Snapshot) bool {
		b, _ := json.Marshal(snap)
		if _, err := fmt.Fprintf(w, "event: snapshot\ndata: %s\n\n", b); err != nil {
			return false
		}
		fl.Flush()
		return true
	}
	if !send(s.poller.get()) {
		return
	}
	for {
		select {
		case <-r.Context().Done():
			return
		case snap := <-ch:
			// Stop streaming as soon as the session ends.
			if _, ok := s.auth.Lookup(token); !ok {
				return
			}
			if !send(snap) {
				return
			}
		}
	}
}

const auditDownMsg = "The action wasn't run because it couldn't be written to the audit log. Check free disk space and the data folder's permissions."

func (s *Server) handleAction(w http.ResponseWriter, r *http.Request) {
	sess := sessionFrom(r)
	req, err := actions.Parse(r.Body)
	if err != nil {
		_ = s.record(r, sess.User, "action.rejected", "", audit.Denied, err.Error())
		writeError(w, http.StatusBadRequest, "That action isn't available.")
		return
	}
	if sess.Scope == auth.ScopeSetup && !req.AllowedDuringSetup() {
		_ = s.record(r, sess.User, string(req.Action), req.Target(), audit.Denied, "not allowed during setup")
		writeError(w, http.StatusForbidden, "Create the Admin account first. This action is only for the Admin.")
		return
	}
	// Write the intent before doing anything; refuse if that fails.
	if err := s.record(r, sess.User, string(req.Action), req.Target(), audit.Requested, ""); err != nil {
		s.log.Error("audit write failed", "err", err)
		writeError(w, http.StatusServiceUnavailable, auditDownMsg)
		return
	}

	switch req.Action {
	case catalog.DiagnosticsBundle:
		s.serveDiagnostics(w, r, sess)
		return
	case catalog.CertRenew:
		if err := s.renewCert(); err != nil {
			s.log.Error("renew certificate", "err", err)
			_ = s.record(r, sess.User, string(req.Action), "", audit.Failed, err.Error())
			writeError(w, http.StatusInternalServerError, "The certificate couldn't be renewed. Check that the data folder's certs folder exists and isn't read-only.")
			return
		}
		_ = s.record(r, sess.User, string(req.Action), "", audit.Succeeded, "")
		s.poller.invalidateChecks()
		s.poller.refresh(r.Context())
		writeJSON(w, http.StatusOK, map[string]string{"message": "Certificate renewed. Restart the web app so it picks up the new certificate."})
		return
	case catalog.SetLANBinding:
		st, err := s.updateState(func(st *config.State) { st.LANEnabled = *req.Enabled })
		if err != nil {
			_ = s.record(r, sess.User, string(req.Action), req.Target(), audit.Failed, err.Error())
			writeError(w, http.StatusInternalServerError, "The setting couldn't be saved. Check that the data folder isn't read-only.")
			return
		}
		_ = s.reconcileListeners()
		_ = s.record(r, sess.User, string(req.Action), req.Target(), audit.Succeeded, "")
		s.poller.refresh(r.Context())
		msg := "The Control Center now only accepts connections from this computer."
		if st.LANEnabled {
			msg = "The Control Center now also accepts connections from your local network."
		}
		writeJSON(w, http.StatusOK, map[string]string{"message": msg})
		return
	}

	if req.Action == catalog.ComponentInstall || req.Action == catalog.InstallMissing {
		var ids []catalog.ComponentID
		if req.Component != nil {
			ids = []catalog.ComponentID{req.Component.ID}
		} else {
			ids = s.comps.Needed()
		}
		op, err := s.startInstall(req.Action, ids, sess.User, netguard.ClientIP(r).String())
		var busy errBusy
		if errors.As(err, &busy) {
			_ = s.record(r, sess.User, string(req.Action), req.Target(), audit.Denied, "another action running")
			writeError(w, http.StatusConflict, "Another action is still running: "+busy.op.Message+" Wait for it to finish, then try again.")
			return
		}
		s.poller.refresh(r.Context())
		writeJSON(w, http.StatusAccepted, op)
		return
	}

	var ids []catalog.ServiceID
	label := "All services"
	var svcID catalog.ServiceID
	switch req.Action {
	case catalog.StackStartAll:
		for _, svc := range startOrder() {
			ids = append(ids, svc.ID)
		}
	case catalog.StackStopAll:
		order := startOrder()
		for i := len(order) - 1; i >= 0; i-- {
			ids = append(ids, order[i].ID)
		}
	case catalog.ServiceStart, catalog.ServiceStop, catalog.ServiceRestart:
		svcID, label = req.Service.ID, req.Service.Name
		ids = []catalog.ServiceID{svcID}
	default:
		writeError(w, http.StatusBadRequest, "That action isn't available.")
		return
	}
	op, err := s.ops.start(req.Action, svcID, sess.User, describe(req.Action, label, true), s.now())
	var busy errBusy
	if errors.As(err, &busy) {
		_ = s.record(r, sess.User, string(req.Action), req.Target(), audit.Denied, "another action running")
		writeError(w, http.StatusConflict, "Another action is still running: "+busy.op.Message+" Wait for it to finish, then try again.")
		return
	}
	ip := netguard.ClientIP(r).String()
	started := *op // copy before the goroutine can change it
	go s.runServices(s.bgCtx, op, req.Action, ids, sess.User, ip, label)
	s.poller.refresh(r.Context())
	writeJSON(w, http.StatusAccepted, started)
}

// startOrder is the order services start in: the models and web app first,
// so the worker finds them when it starts.
func startOrder() []catalog.Service {
	order := []catalog.ServiceID{catalog.Models, catalog.API, catalog.Worker}
	out := make([]catalog.Service, 0, len(order))
	for _, id := range order {
		if svc, ok := catalog.Lookup(string(id)); ok {
			out = append(out, svc)
		}
	}
	return out
}

func describe(a catalog.ActionName, label string, running bool) string {
	verbs := map[catalog.ActionName][2]string{
		catalog.StackStartAll:  {"Starting all services.", "All services started."},
		catalog.StackStopAll:   {"Stopping all services.", "All services stopped."},
		catalog.ServiceStart:   {"Starting " + label + ".", label + " started."},
		catalog.ServiceStop:    {"Stopping " + label + ".", label + " stopped."},
		catalog.ServiceRestart: {"Restarting " + label + ".", label + " restarted."},
	}
	v := verbs[a]
	if running {
		return v[0]
	}
	return v[1]
}

// ReadyTimeout is how long a start waits for services to pass health checks.
var ReadyTimeout = 3 * time.Minute

// runServices performs a start, stop or restart and records the outcome.
func (s *Server) runServices(ctx context.Context, op *Operation, action catalog.ActionName, ids []catalog.ServiceID, actor, ip, label string) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	target := ""
	if len(ids) == 1 && (action == catalog.ServiceStart || action == catalog.ServiceStop || action == catalog.ServiceRestart) {
		target = string(ids[0])
	}
	entry := audit.Entry{Actor: actor, IP: ip, Action: string(action), Target: target}
	fail := func(msg string, err error) {
		s.log.Error("service action failed", "action", action, "target", target, "err", err)
		entry.Outcome, entry.Detail = audit.Failed, msg
		_ = s.audit.Append(entry)
		s.ops.finish(op, false, msg, s.now())
		s.poller.refresh(s.bgCtx)
	}

	stopping := action == catalog.StackStopAll || action == catalog.ServiceStop || action == catalog.ServiceRestart
	starting := action != catalog.StackStopAll && action != catalog.ServiceStop

	// Remember what should run after a restart of the computer.
	_, _ = s.updateState(func(st *config.State) {
		set := map[string]bool{}
		for _, id := range st.Wanted {
			set[id] = true
		}
		for _, id := range ids {
			set[string(id)] = starting
		}
		st.Wanted = st.Wanted[:0]
		for _, svc := range catalog.Services {
			if set[string(svc.ID)] {
				st.Wanted = append(st.Wanted, string(svc.ID))
			}
		}
	})

	if stopping {
		for _, id := range ids {
			if err := s.sup.Stop(ctx, id); err != nil {
				fail(serviceName(id)+" didn't stop: "+plain(err)+" Try again, or restart the computer.", err)
				return
			}
		}
	}
	if starting {
		for _, id := range ids {
			if err := s.sup.Start(ctx, id); err != nil {
				fail(serviceName(id)+" didn't start: "+plain(err), err)
				return
			}
			s.poller.refresh(s.bgCtx)
		}
		if msg, err := s.waitReady(ctx, ids); err != nil {
			fail(msg, err)
			return
		}
	}
	entry.Outcome = audit.Succeeded
	_ = s.audit.Append(entry)
	s.ops.finish(op, true, describe(action, label, false), s.now())
	s.poller.refresh(s.bgCtx)
}

// waitReady waits until every service passes its health check, or one of
// them fails, and returns a plain next step on failure.
func (s *Server) waitReady(ctx context.Context, ids []catalog.ServiceID) (string, error) {
	deadline := s.now().Add(ReadyTimeout)
	for {
		s.poller.refresh(ctx)
		ready := true
		for _, id := range ids {
			st := s.poller.rawState(id)
			switch st.State {
			case status.Running:
			case status.Error:
				msg := st.Name + " didn't start properly."
				if st.Problem != nil {
					msg = st.Problem.What + " " + st.Problem.Next
				}
				return msg, errors.New(st.Detail)
			default:
				ready = false
			}
		}
		if ready {
			return "", nil
		}
		if s.now().After(deadline) {
			var slow []string
			for _, id := range ids {
				if st := s.poller.rawState(id); st.State != status.Running {
					slow = append(slow, st.Name)
				}
			}
			return strings.Join(slow, ", ") + " didn't become ready within " + ReadyTimeout.String() + ". Open the log to see why, then restart it.", errors.New("timeout")
		}
		select {
		case <-ctx.Done():
			return "The action was interrupted because the launcher is stopping.", ctx.Err()
		case <-time.After(time.Second):
		}
	}
}

func serviceName(id catalog.ServiceID) string {
	if svc, ok := catalog.Lookup(string(id)); ok {
		return svc.Name
	}
	return string(id)
}

// plain passes through messages written for people and hides system errors.
func plain(err error) string {
	msg := err.Error()
	if strings.HasSuffix(msg, ".") && strings.ToUpper(msg[:1]) == msg[:1] {
		return msg
	}
	return "Windows reported an error. Open the launcher log in diagnostics for details."
}

func (s *Server) handleLogs(w http.ResponseWriter, r *http.Request) {
	svc, ok := catalog.Lookup(r.URL.Query().Get("service"))
	if !ok {
		writeError(w, http.StatusBadRequest, "Choose a service.")
		return
	}
	tail, _ := strconv.Atoi(r.URL.Query().Get("tail"))
	if tail <= 0 || tail > 5000 {
		tail = 1000
	}
	lines, err := s.serviceLogs(r.Context(), svc, tail)
	if err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"service": svc.ID, "lines": lines})
}

type logLine struct {
	Time   string `json:"time"`
	Stream string `json:"stream"`
	Level  string `json:"level"`
	Text   string `json:"text"`
}

func (s *Server) serviceLogs(_ context.Context, svc catalog.Service, tail int) ([]logLine, error) {
	raw, err := procs.Tail(procs.LogFile(s.cfg, svc.ID), tail)
	if err != nil {
		return nil, errors.New("The log couldn't be read. Try again in a moment.")
	}
	out := make([]logLine, 0, len(raw))
	for _, l := range raw {
		text := redact.String(l.Text) // already redacted on write; belt and braces
		out = append(out, logLine{Time: l.Time, Stream: l.Stream, Level: levelOf(text, l.Stream), Text: text})
	}
	return out, nil
}

func levelOf(text, stream string) string {
	low := strings.ToLower(text)
	switch {
	case strings.Contains(low, "error"), strings.Contains(low, "fatal"), strings.Contains(low, "traceback"), strings.Contains(low, "panic"):
		return "error"
	case strings.Contains(low, "warn"):
		return "warning"
	case strings.Contains(low, "debug"):
		return "debug"
	}
	_ = stream
	return "info"
}

func (s *Server) handleAudit(w http.ResponseWriter, r *http.Request) {
	entries, err := s.audit.Recent(50)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "The audit log couldn't be read.")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"entries": entries})
}

// handleQR renders the n-th LAN address of the web app as an SVG QR code.
// The URL is computed by the server; nothing from the request is encoded.
func (s *Server) handleQR(w http.ResponseWriter, r *http.Request) {
	urls := s.lanInfo().AppURLs
	i, _ := strconv.Atoi(r.URL.Query().Get("i"))
	if i < 0 || i >= len(urls) {
		writeError(w, http.StatusNotFound, "No network address found.")
		return
	}
	code, err := qr.Encode(urls[i], qr.M)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "The QR code couldn't be drawn.")
		return
	}
	const quiet = 4
	n := code.Size + 2*quiet
	var b strings.Builder
	fmt.Fprintf(&b, `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" shape-rendering="crispEdges"><rect width="%d" height="%d" fill="#fff"/><path fill="#000" d="`, n, n, n, n)
	for y := 0; y < code.Size; y++ {
		for x := 0; x < code.Size; x++ {
			if code.Black(x, y) {
				fmt.Fprintf(&b, "M%d %dh1v1h-1z", x+quiet, y+quiet)
			}
		}
	}
	b.WriteString(`"/></svg>`)
	w.Header().Set("Content-Type", "image/svg+xml")
	_, _ = io.WriteString(w, b.String())
}

// handleCACert lets the Admin download the local CA certificate (public, no
// key) to install on other devices.
func (s *Server) handleCACert(w http.ResponseWriter, r *http.Request) {
	b, err := os.ReadFile(filepath.Join(s.cfg.CertDir(), "ca.crt"))
	if err != nil {
		writeError(w, http.StatusNotFound, "The certificate authority file is missing. Run the installer again.")
		return
	}
	w.Header().Set("Content-Type", "application/x-x509-ca-cert")
	w.Header().Set("Content-Disposition", `attachment; filename="case-file-manager-ca.crt"`)
	_, _ = w.Write(b)
}

func (s *Server) serveDiagnostics(w http.ResponseWriter, r *http.Request, sess *auth.Session) {
	snap := s.poller.get()
	w.Header().Set("Content-Type", "application/zip")
	name := "diagnostics-" + s.now().UTC().Format("20060102-150405") + ".zip"
	w.Header().Set("Content-Disposition", `attachment; filename="`+name+`"`)
	zw := zip.NewWriter(w)
	add := func(name string, b []byte) {
		f, err := zw.Create(name)
		if err == nil {
			_, _ = f.Write(b)
		}
	}
	add("README.txt", []byte(diagnosticsReadme))
	status, _ := json.MarshalIndent(map[string]any{
		"generatedAt": snap.GeneratedAt, "launcherVersion": snap.Launcher,
		"runtime": snap.Runtime, "services": snap.Services, "checks": snap.Checks,
		"operations": snap.Operations, "lanControlCenter": snap.LAN.ControlCenterOnLAN,
	}, "", "  ")
	add("status.json", status)
	cfg, _ := json.MarshalIndent(map[string]any{
		"supervisor": s.cfg.Supervisor, "port": s.cfg.Port, "appPort": s.cfg.AppPort,
		"modelsPort": s.cfg.ModelsPort, "embeddingModel": s.cfg.EmbeddingModel, "hostname": s.cfg.Hostname,
	}, "", "  ")
	add("config.json", cfg)
	for _, svc := range catalog.Services {
		lines, err := s.serviceLogs(r.Context(), svc, 2000)
		var b strings.Builder
		if err != nil {
			b.WriteString(err.Error() + "\n")
		}
		for _, l := range lines {
			fmt.Fprintf(&b, "%s %s %s\n", l.Time, l.Stream, l.Text)
		}
		add("logs/"+string(svc.ID)+".log", []byte(b.String()))
	}
	if lb, err := os.ReadFile(filepath.Join(s.cfg.LauncherDir(), "launcher.log")); err == nil {
		if len(lb) > 2<<20 {
			lb = lb[len(lb)-2<<20:]
		}
		add("logs/launcher.log", []byte(redact.String(string(lb))))
	}
	if err := zw.Close(); err != nil {
		_ = s.record(r, sess.User, string(catalog.DiagnosticsBundle), "", audit.Failed, err.Error())
		return
	}
	_ = s.record(r, sess.User, string(catalog.DiagnosticsBundle), "", audit.Succeeded, "")
}

const diagnosticsReadme = `Case File Manager diagnostics

This bundle helps troubleshoot the services. It contains service status,
system checks, recent service logs and the launcher log.

It does NOT contain case files, document text, API keys or passwords.
Secrets and case-file paths are removed from the logs before they are
written here. Review the files before sharing them with anyone.
`

func (s *Server) handleStatic(w http.ResponseWriter, r *http.Request) {
	if strings.HasPrefix(r.URL.Path, "/api/") {
		writeError(w, http.StatusNotFound, "That page doesn't exist.")
		return
	}
	p := strings.TrimPrefix(path.Clean(r.URL.Path), "/")
	if p == "" {
		p = "index.html"
	}
	if st, err := fs.Stat(s.web, p); err != nil || st.IsDir() {
		p = "index.html" // client-side routes
	}
	b, err := fs.ReadFile(s.web, p)
	if err != nil {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = io.WriteString(w, "The Control Center page wasn't built into this launcher. Build it with scripts/build.sh.\n")
		return
	}
	if ct := mime.TypeByExtension(path.Ext(p)); ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	if strings.HasPrefix(p, "assets/") {
		w.Header().Set("Cache-Control", "public, max-age=31536000, immutable")
	} else {
		w.Header().Set("Cache-Control", "no-cache")
	}
	_, _ = w.Write(b)
}
