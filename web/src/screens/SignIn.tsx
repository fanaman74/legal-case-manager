import { useState, type FormEvent } from "react";
import { api, type Session } from "../api";
import { useTitle } from "../hooks";
import { Button, Field, FormError, Icon } from "../ui";

const MIN_PASSWORD = 12;

export function SignIn({ onSession, notice }: { onSession: (s: Session) => void; notice?: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useTitle("Sign in");

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSession(await api.signIn(username.trim(), password));
    } catch (err) {
      setError((err as Error).message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <form className="auth__card" onSubmit={submit} noValidate>
        <div className="auth__brand"><span className="brand__mark" aria-hidden="true" /> Case File Manager</div>
        <h1 className="auth__title">Sign in</h1>
        {notice && !error && <p className="banner banner--info" role="status"><Icon name="lock" />{notice}</p>}
        <FormError message={error} />
        <Field id="username" label="Username">
          <input id="username" className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus />
        </Field>
        <Field id="password" label="Password">
          <input id="password" type="password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </Field>
        <Button type="submit" variant="primary" busy={busy} disabled={!username.trim() || !password}>
          Sign in
        </Button>
        <p className="auth__foot">Forgotten your password? Ask the Admin to reset it.</p>
      </form>
    </main>
  );
}

export function NeedsSetup({ onRetry }: { onRetry: () => void }) {
  useTitle("Finish setup");
  return (
    <main className="auth">
      <div className="auth__card">
        <div className="auth__brand"><span className="brand__mark" aria-hidden="true" /> Case File Manager</div>
        <h1 className="auth__title">Finish setup first</h1>
        <p className="auth__lead">
          Nobody can sign in until the Admin account exists. On the host computer, open the Control Center (the Case File Manager shortcut on the desktop) and complete the setup wizard, then come back here.
        </p>
        <Button variant="primary" onClick={onRetry}>Check again</Button>
      </div>
    </main>
  );
}

/** Choose a new password. `first` is the full-screen version shown after signing in with a temporary password. */
export function ChoosePassword({ first, onDone, onSignOut }: { first?: boolean; onDone: (s: Session) => void; onSignOut?: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useTitle(first ? "Choose your password" : "Change password");
  const mismatch = confirm.length > 0 && confirm !== next;
  const short = next.length > 0 && next.length < MIN_PASSWORD;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (mismatch || short) return;
    setBusy(true);
    setError(null);
    try {
      const s = await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      onDone(s);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const form = (
    <form className={first ? "auth__card" : "form-grid"} onSubmit={submit} noValidate>
      {first && <div className="auth__brand"><span className="brand__mark" aria-hidden="true" /> Case File Manager</div>}
      {first && <h1 className="auth__title">Choose your password</h1>}
      {first && <p className="auth__lead">You signed in with a temporary password. Choose your own to continue. Nobody else, including the Admin, will know it.</p>}
      <FormError message={error} />
      <Field id="pw-current" label={first ? "Temporary password" : "Current password"}>
        <input id="pw-current" type="password" className="input" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" autoFocus={first} />
      </Field>
      <Field id="pw-new" label="New password" hint={short ? <span className="field__hint--error">Use at least {MIN_PASSWORD} characters.</span> : `At least ${MIN_PASSWORD} characters. A short sentence is easy to remember and hard to guess.`}>
        <input id="pw-new" type="password" className="input" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" aria-describedby="pw-new-hint" aria-invalid={short || undefined} />
      </Field>
      <Field id="pw-confirm" label="Type the new password again" hint={mismatch ? <span className="field__hint--error">The two passwords don't match.</span> : undefined}>
        <input id="pw-confirm" type="password" className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" aria-invalid={mismatch || undefined} />
      </Field>
      <div className="btn-row">
        <Button type="submit" variant="primary" busy={busy} disabled={!current || next.length < MIN_PASSWORD || confirm !== next}>
          {first ? "Save and continue" : "Change password"}
        </Button>
        {onSignOut && <Button variant="quiet" onClick={onSignOut}>Sign out</Button>}
      </div>
    </form>
  );
  return first ? <main className="auth">{form}</main> : <PasswordPage>{form}</PasswordPage>;
}

function PasswordPage({ children }: { children: React.ReactNode }) {
  return (
    <div className="page">
      <header className="page__head">
        <h1 className="page__title">Change password</h1>
      </header>
      {children}
    </div>
  );
}
