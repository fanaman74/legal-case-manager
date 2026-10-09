import { useState } from "react";
import type { Snapshot } from "../api";
import type { RunAction } from "../screens/ControlCenter";
import { Button, ConfirmDialog, CopyButton, Icon, Section } from "../ui";

export function Network({ snap, run }: { snap: Snapshot; run: RunAction }) {
  const [confirmLan, setConfirmLan] = useState(false);
  const [busy, setBusy] = useState(false);
  const lan = snap.lan;
  const cert = snap.checks.find((c) => c.id === "cert");

  const setLan = async (enabled: boolean) => {
    setBusy(true);
    await run("launcher.set_lan_binding", { enabled });
    setBusy(false);
  };

  return (
    <Section id="network" title="Network" meta="How other people reach the case manager">
      <div className="net">
        <div className="net__block">
          <h3 className="h3">Address to share</h3>
          {lan.appUrls.length === 0 ? (
            <p className="empty-inline">
              This computer isn't connected to a local network, so there is no address to share yet. Connect to your office network, and the address appears here.
            </p>
          ) : (
            <>
              <p className="muted">People on your office network open this address in their browser. It only works on the same network, or through your VPN.</p>
              <ul className="addr-list">
                {lan.appUrls.map((u, i) => (
                  <li key={u} className="addr">
                    <img className="addr__qr" src={`/api/lan-qr?i=${i}`} alt={`QR code for ${u}`} width={96} height={96} />
                    <div className="addr__text">
                      <span className="addr__url mono">{u}</span>
                      <CopyButton text={u} label="Copy address" />
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="net__block">
          <h3 className="h3">Trusting the certificate</h3>
          <p className="muted">
            Browsers on other devices warn about the certificate until they trust this computer's certificate authority. Install it once on each device.
          </p>
          <p className="net__cert">
            <span className="mono">{cert?.detail}</span>
          </p>
          <div className="btn-row">
            <a className="btn btn--secondary btn--sm" href="/api/ca-certificate" download>
              <Icon name="download" />
              <span>Download certificate authority</span>
            </a>
            <Button size="sm" icon="refresh" onClick={() => void run("cert.renew")}>Renew certificate</Button>
          </div>
        </div>

        <div className="net__block">
          <h3 className="h3">Control Center access</h3>
          <div className="switch-row">
            <div>
              <p className="switch-row__label" id="lan-label">Allow the Control Center from other devices on the local network</p>
              <p className="muted" id="lan-desc">
                {lan.controlCenterOnLan
                  ? "On. You can also open the Control Center from another device on your network. You still need to sign in."
                  : "Off. The Control Center only opens on this computer. This is the safest setting."}
              </p>
              {lan.controlCenterOnLan && lan.controlCenterUrls.length > 0 && (
                <p className="mono muted">{lan.controlCenterUrls.join("  ·  ")}</p>
              )}
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={lan.controlCenterOnLan}
              aria-labelledby="lan-label"
              aria-describedby="lan-desc"
              className="switch"
              disabled={busy}
              onClick={() => (lan.controlCenterOnLan ? void setLan(false) : setConfirmLan(true))}
            >
              <span className="switch__thumb" />
              <span className="switch__text">{lan.controlCenterOnLan ? "On" : "Off"}</span>
            </button>
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmLan}
        title="Allow the Control Center from your network?"
        body={
          <p>
            Anyone on your local network will be able to reach the Control Center sign-in page. They still need the Admin password. Leave this off unless you need to manage services from another device.
          </p>
        }
        confirmLabel="Allow from network"
        onConfirm={() => {
          setConfirmLan(false);
          void setLan(true);
        }}
        onCancel={() => setConfirmLan(false)}
      />
    </Section>
  );
}
