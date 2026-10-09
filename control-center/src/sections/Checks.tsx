import type { Snapshot } from "../api";
import type { RunAction } from "../screens/ControlCenter";
import { Button, checkIcon, Icon, Section } from "../ui";

export function Checks({ snap, run, isAdmin }: { snap: Snapshot; run: RunAction; isAdmin: boolean }) {
  const attention = snap.checks.filter((c) => c.level === "fail" || c.level === "warn").length;
  return (
    <Section id="checks" title="System checks" meta={attention ? `${attention} to review` : "Nothing needs attention"}>
      <ul className="checks">
        {snap.checks.map((c) => {
          const [tone, icon, label] = checkIcon[c.level];
          return (
            <li key={c.id} className={`check check--${c.level}`}>
              <span className={`check__icon tone--${tone}`}><Icon name={icon} label={label} /></span>
              <span className="check__label">{c.label}</span>
              <span className="check__detail">
                {c.detail}
                {c.next && <span className="check__next">{c.next}</span>}
              </span>
              <span className="check__action">
                {isAdmin && c.id === "cert" && c.level !== "pass" && (
                  <Button size="sm" icon="refresh" onClick={() => void run("cert.renew")}>Renew certificate</Button>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}
