import { useState, type FormEvent } from "react";
import { api, type Session } from "../api";
import { Button } from "../ui";

export function SignIn({ onSession }: { onSession: (s: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSession(await api.login(username, password));
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
        <p className="eyebrow">Case File Manager</p>
        <h1 className="auth__title">Sign in to the Control Center</h1>
        <p className="auth__lead">Only the Admin can start and stop services from here.</p>
        {error && <p className="field__error field__error--block" role="alert">{error}</p>}
        <div className="field">
          <label htmlFor="username">Username</label>
          <input id="username" className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoFocus />
        </div>
        <div className="field">
          <label htmlFor="password">Password</label>
          <input id="password" type="password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </div>
        <Button type="submit" variant="primary" busy={busy} disabled={!username || !password}>
          Sign in
        </Button>
      </form>
    </main>
  );
}
