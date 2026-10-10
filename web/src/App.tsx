import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError, setSignedOutHandler, type Case, type Session } from "./api";
import { AppContext, type AppState } from "./hooks";
import { href, useRoute } from "./router";
import { CaseList } from "./screens/CaseList";
import { CasePage } from "./screens/CasePage";
import { AuditLog } from "./screens/AuditLog";
import { People } from "./screens/People";
import { ChoosePassword, NeedsSetup, SignIn } from "./screens/SignIn";
import { Shell } from "./components/Shell";
import { EmptyState, ErrorPanel, Link } from "./ui";

type Boot = { state: "loading" } | { state: "signed-out"; adminReady: boolean; notice?: string } | { state: "signed-in"; session: Session } | { state: "error"; message: string };

export function App() {
  const [boot, setBoot] = useState<Boot>({ state: "loading" });

  const signedOut = useCallback(async (notice?: string) => {
    try {
      const { admin_ready } = await api.setup();
      setBoot({ state: "signed-out", adminReady: admin_ready, notice });
    } catch (e) {
      setBoot({ state: "error", message: (e as Error).message });
    }
  }, []);

  const start = useCallback(async () => {
    try {
      setBoot({ state: "signed-in", session: await api.session() });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) await signedOut();
      else setBoot({ state: "error", message: (e as Error).message });
    }
  }, [signedOut]);

  useEffect(() => {
    void start();
    setSignedOutHandler(() => void signedOut("You were signed out. Sign in again to continue."));
  }, [start, signedOut]);

  switch (boot.state) {
    case "loading":
      return <main className="auth" aria-busy="true" />;
    case "error":
      return (
        <main className="auth">
          <div className="auth__card">
            <ErrorPanel message={boot.message} onRetry={() => void start()} />
          </div>
        </main>
      );
    case "signed-out":
      return boot.adminReady ? (
        <SignIn notice={boot.notice} onSession={(session) => setBoot({ state: "signed-in", session })} />
      ) : (
        <NeedsSetup onRetry={() => void signedOut()} />
      );
    case "signed-in":
      if (boot.session.user.must_change_password) {
        return <ChoosePassword first onDone={(session) => setBoot({ state: "signed-in", session })} onSignOut={() => void api.signOut().finally(() => signedOut())} />;
      }
      return <SignedIn session={boot.session} onSignedOut={() => void signedOut()} onSession={(session) => setBoot({ state: "signed-in", session })} />;
  }
}

function SignedIn({ session, onSignedOut, onSession }: { session: Session; onSignedOut: () => void; onSession: (s: Session) => void }) {
  const route = useRoute();
  const [cases, setCases] = useState<Case[]>([]);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const reloadCases = useCallback(async () => {
    try {
      setCases((await api.cases()).items);
    } catch {
      // the page that needs the list shows its own error
    }
  }, []);
  useEffect(() => {
    void reloadCases();
  }, [reloadCases]);

  useEffect(() => {
    if (!toastMsg) return;
    const t = setTimeout(() => setToastMsg(null), 4000);
    return () => clearTimeout(t);
  }, [toastMsg]);

  const state: AppState = useMemo(
    () => ({
      user: session.user,
      cases,
      reloadCases,
      signOut: () => void api.signOut().finally(onSignedOut),
      toast: setToastMsg,
    }),
    [session.user, cases, reloadCases, onSignedOut],
  );

  const isAdmin = session.user.role === "admin";
  let page;
  switch (route.page) {
    case "cases":
      page = <CaseList />;
      break;
    case "case":
      page = <CasePage key={route.id} id={route.id} tab={route.tab} folder={route.folder} />;
      break;
    case "people":
      page = isAdmin ? <People /> : <NoAccess />;
      break;
    case "audit":
      page = isAdmin ? <AuditLog /> : <NoAccess />;
      break;
    case "password":
      page = session.user.managed_by_control_center ? (
        <EmptyState icon="key" title="The Admin password is changed in the Control Center.">
          Open the Control Center on the host computer and use its account settings. The new password then works here too.
        </EmptyState>
      ) : (
        <ChoosePassword onDone={(s) => { onSession(s); setToastMsg("Password changed."); }} />
      );
      break;
    default:
      page = (
        <EmptyState icon="file" title="There's no page at this address." action={<Link to={href({ page: "cases" })}>Go to your cases</Link>} />
      );
  }

  return (
    <AppContext.Provider value={state}>
      <Shell route={route}>{page}</Shell>
      <div className="toast-region" aria-live="polite">
        {toastMsg && <div className="toast">{toastMsg}</div>}
      </div>
    </AppContext.Provider>
  );
}

function NoAccess() {
  return <EmptyState icon="lock" title="Only the Admin can open this page." action={<Link to="/">Go to your cases</Link>} />;
}
