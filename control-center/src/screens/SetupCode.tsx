import { useState, type FormEvent } from "react";
import { api, type Session } from "../api";
import { Button } from "../ui";

export function SetupCode({ onSession }: { onSession: (s: Session) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSession(await api.redeemSetupCode(code));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth">
      <form className="auth__card" onSubmit={submit} noValidate>
        <p className="eyebrow">First-time setup</p>
        <h1 className="auth__title">Enter your setup code</h1>
        <p className="auth__lead">
          The installer showed a 12-character code when it finished. It is also saved in{" "}
          <span className="mono">setup-code.txt</span> in the launcher folder
          (<span className="mono">C:\CaseFiles\launcher</span> on a standard install). Only someone at this computer can read it.
        </p>
        <div className="field">
          <label htmlFor="setup-code">Setup code</label>
          <input
            id="setup-code"
            className="input input--code mono"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="one-time-code"
            autoCapitalize="characters"
            spellCheck={false}
            placeholder="XXXX-XXXX-XXXX"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "setup-code-error" : undefined}
            autoFocus
          />
          {error && <p id="setup-code-error" className="field__error" role="alert">{error}</p>}
        </div>
        <Button type="submit" variant="primary" busy={busy} disabled={code.trim().length < 12}>
          Continue
        </Button>
      </form>
    </main>
  );
}
