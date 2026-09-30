import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Session } from "../api";
import { Button } from "../ui";

export function SetupCode({ onSession }: { onSession: (s: Session) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const redeem = async (value: string) => {
    setBusy(true);
    setError(null);
    try {
      onSession(await api.redeemSetupCode(value));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    void redeem(code);
  };

  // Setup.exe opens this page with the code in the address fragment, which
  // is never sent to a server. Take it out of the address bar and history,
  // then sign in with it so nobody has to type it.
  const tried = useRef(false);
  useEffect(() => {
    if (tried.current) return;
    tried.current = true;
    const passed = codeFromHash(window.location.hash);
    if (!passed) return;
    history.replaceState(null, "", window.location.pathname + window.location.search);
    setCode(passed);
    void redeem(passed);
  }, []);

  return (
    <main className="auth">
      <form className="auth__card" onSubmit={submit} noValidate>
        <p className="eyebrow">First-time setup</p>
        <h1 className="auth__title">Enter your setup code</h1>
        <p className="auth__lead">
          Setup showed a 12-character code when it finished. It is also saved in{" "}
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

/** Reads the setup code Setup.exe puts in the address, as #setup=XXXX-XXXX-XXXX. */
export function codeFromHash(hash: string): string | null {
  const m = /^#setup=([0-9A-Z]{4}-[0-9A-Z]{4}-[0-9A-Z]{4})$/.exec(hash);
  return m ? m[1] : null;
}
