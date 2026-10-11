// Where the app finds the Case File Manager services on this computer. The
// installer runs Setup.exe with its standard ports; the environment
// variables are for development and tests only.
import path from "node:path";

export type Config = {
  /** The case manager (web app), served by the API service. */
  appUrl: string;
  /** The Control Center, served by the launcher on this computer only. */
  controlCenterUrl: string;
  /** The local CA certificate the installer copied next to the app. */
  caPath: string;
  /** First-run file the installer leaves with the Control Center address and setup code. */
  handoffPath: string;
};

function port(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 && n < 65536 ? n : fallback;
}

export function origin(p: number): string {
  return p === 443 ? "https://localhost/" : `https://localhost:${p}/`;
}

export function loadConfig(env: NodeJS.ProcessEnv, resourcesPath: string, tmpDir: string): Config {
  return {
    appUrl: origin(port(env.CASEFILES_APP_PORT, 443)),
    controlCenterUrl: origin(port(env.CASEFILES_CC_PORT, 8443)),
    caPath: env.CASEFILES_CA || path.join(resourcesPath, "local-ca.crt"),
    handoffPath: path.join(tmpDir, "CaseFileManager-setup.txt"),
  };
}
