// Case File Manager's desktop app. It is only a window onto the services
// already running on this computer (the launcher, web app, worker and
// local AI models start with Windows), so closing it stops nothing.
//
// The window shows two local addresses and nothing else: the case manager
// and the Control Center. It trusts only this computer's own certificate
// authority, refuses every request to anywhere else, opens other links in
// the normal browser, and has no Node.js or preload bridge in its pages.
import { app, BrowserWindow, Menu, Notification, Tray, nativeImage, nativeTheme, session, shell, type Session } from "electron";
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { loadConfig } from "./config";
import { classify, handoffUrl, mayRequest } from "./policy";
import { finished, health, healthText, snapshot, type Health, type JobSnapshot, type JobStatus } from "./status";
import { verifyServerCertificate } from "./trust";

const cfg = loadConfig(process.env, process.resourcesPath, os.tmpdir());
const staticDir = path.join(__dirname, "..", "static");
const offlinePage = path.join(staticDir, "offline.html");
const smokeOut = process.argv.find((a) => a.startsWith("--smoke-test="))?.slice("--smoke-test=".length);

let caPem = "";
let mainWindow: BrowserWindow | null = null;
let ccWindow: BrowserWindow | null = null;
let tray: Tray | null = null;
let state: Health = "stopped";
let jobs: JobSnapshot | null = null;
// Set when the last certificate check failed, so the offline screen can say
// so instead of "not answering".
let certRefused = false;

app.setAppUserModelId("CaseFileManager.Desktop");
if (!smokeOut && !app.requestSingleInstanceLock()) {
  app.quit();
}

function readCA(): string {
  try {
    return readFileSync(cfg.caPath, "utf8");
  } catch {
    return "";
  }
}

function lockDown(ses: Session): void {
  ses.setCertificateVerifyProc((req, done) => {
    if (!caPem) caPem = readCA();
    const v = caPem ? verifyServerCertificate(req.certificate.data, caPem, req.hostname) : { ok: false as const, reason: "no local CA" };
    if (!v.ok) console.warn(`certificate refused for ${req.hostname}: ${v.reason}`);
    certRefused = !v.ok;
    done(v.ok ? 0 : -2);
  });
  ses.webRequest.onBeforeRequest((details, done) => {
    done({ cancel: !mayRequest(details.url, cfg.appUrl, cfg.controlCenterUrl) });
  });
  // Pages may copy to the clipboard and go full screen; no camera,
  // microphone, location, notifications or anything else.
  const allowed = new Set(["clipboard-sanitized-write", "fullscreen"]);
  ses.setPermissionRequestHandler((_wc, permission, done) => done(allowed.has(permission)));
  ses.setPermissionCheckHandler((_wc, permission) => allowed.has(permission));
  ses.on("will-download", (_e, item) => {
    item.once("done", (_ev, result) => {
      if (result !== "completed") return;
      const saved = item.getSavePath();
      const n = new Notification({ title: "Download saved", body: `${path.basename(saved)}. Click to show it in its folder.` });
      n.on("click", () => shell.showItemInFolder(saved));
      n.show();
    });
  });
}

function createWindow(url: string, kind: "app" | "control-center"): BrowserWindow {
  const win = new BrowserWindow({
    width: kind === "app" ? 1360 : 1180,
    height: 880,
    minWidth: 900,
    minHeight: 600,
    show: !smokeOut,
    title: kind === "app" ? "Case File Manager" : "Case File Manager Control Center",
    icon: path.join(staticDir, "icon.png"),
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#131311" : "#F6F5F1",
    autoHideMenuBar: false,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false,
      spellcheck: true,
      devTools: !app.isPackaged,
    },
  });
  // Keep the window's own title; the page's title changes as you move around.
  win.on("page-title-updated", (e, title) => {
    e.preventDefault();
    const base = kind === "app" ? "Case File Manager" : "Case File Manager Control Center";
    win.setTitle(!title || title.includes("Case File Manager") ? title || base : `${title} · ${base}`);
  });
  const route = (target: string): boolean => {
    switch (classify(target, cfg.appUrl, cfg.controlCenterUrl)) {
      case "app":
        if (kind === "app") return false;
        showApp(target);
        return true;
      case "control-center":
        if (kind === "control-center") return false;
        showControlCenter(target);
        return true;
      case "local":
        return false;
      case "external":
        void shell.openExternal(target);
        return true;
      default:
        return true;
    }
  };
  win.webContents.on("will-navigate", (e, target) => {
    if (target.startsWith("file:")) {
      // Only the app's own offline screen.
      if (!new URL(target).pathname.endsWith("/static/offline.html")) e.preventDefault();
      return;
    }
    if (route(target)) e.preventDefault();
  });
  win.webContents.setWindowOpenHandler(({ url: target }) => {
    if (!route(target)) {
      // A same-area link that asked for a new tab opens in this window.
      void win.loadURL(target);
    }
    return { action: "deny" };
  });
  win.webContents.on("did-fail-load", (_e, code, _desc, failedUrl, isMainFrame) => {
    if (!isMainFrame || code === -3 /* aborted */) return;
    if (classify(failedUrl, cfg.appUrl, cfg.controlCenterUrl) === "local") return;
    void win.loadFile(offlinePage, {
      query: { area: kind, reason: !(caPem || readCA()) ? "no-ca" : certRefused ? "certificate" : String(code), app: cfg.appUrl, cc: cfg.controlCenterUrl },
    });
  });
  void win.loadURL(url);
  return win;
}

function showApp(url = cfg.appUrl): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (url !== cfg.appUrl || !mainWindow.webContents.getURL().startsWith(cfg.appUrl)) void mainWindow.loadURL(url);
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    return;
  }
  mainWindow = createWindow(url, "app");
  mainWindow.on("closed", () => (mainWindow = null));
}

function showControlCenter(url = cfg.controlCenterUrl): void {
  if (ccWindow && !ccWindow.isDestroyed()) {
    if (url !== cfg.controlCenterUrl) void ccWindow.loadURL(url);
    if (ccWindow.isMinimized()) ccWindow.restore();
    ccWindow.show();
    ccWindow.focus();
    return;
  }
  const win = createWindow(url, "control-center");
  win.on("closed", () => (ccWindow = null));
  win.once("ready-to-show", () => {
    win.moveTop();
    win.focus();
  });
  ccWindow = win;
}

function buildMenu(): void {
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      {
        label: "&File",
        submenu: [
          { label: "Case manager", accelerator: "CmdOrCtrl+1", click: () => showApp() },
          { label: "Control Center", accelerator: "CmdOrCtrl+2", click: () => showControlCenter() },
          { type: "separator" },
          { role: "quit", label: "Quit" },
        ],
      },
      { label: "&Edit", submenu: [{ role: "undo" }, { role: "redo" }, { type: "separator" }, { role: "cut" }, { role: "copy" }, { role: "paste" }, { role: "selectAll" }] },
      {
        label: "&View",
        submenu: [
          { role: "reload" },
          { type: "separator" },
          { role: "resetZoom" },
          { role: "zoomIn" },
          { role: "zoomOut" },
          { type: "separator" },
          { role: "togglefullscreen" },
          ...(app.isPackaged ? [] : [{ role: "toggleDevTools" as const }]),
        ],
      },
      {
        label: "&Help",
        submenu: [
          { label: `Case File Manager ${app.getVersion()}`, enabled: false },
          { label: "Runs on this computer only. Nothing is sent to the internet without your confirmation.", enabled: false },
        ],
      },
    ]),
  );
}

function trayIcon(h: Health) {
  return nativeImage.createFromPath(path.join(staticDir, `tray-${h}.png`));
}

function buildTray(): void {
  tray = new Tray(trayIcon(state));
  tray.setToolTip(healthText[state]);
  tray.on("click", () => showApp());
  updateTrayMenu();
}

function updateTrayMenu(): void {
  tray?.setContextMenu(
    Menu.buildFromTemplate([
      { label: healthText[state], enabled: false },
      { type: "separator" },
      { label: "Open case manager", click: () => showApp() },
      { label: "Open Control Center", click: () => showControlCenter() },
      { type: "separator" },
      { label: "Quit", click: () => app.quit() },
    ]),
  );
}

async function answers(url: string): Promise<Response | null> {
  try {
    return await session.defaultSession.fetch(url, { signal: AbortSignal.timeout(4000), credentials: "include" });
  } catch {
    return null;
  }
}

async function poll(): Promise<void> {
  const [appRes, ccRes] = await Promise.all([answers(new URL("api/setup", cfg.appUrl).href), answers(cfg.controlCenterUrl)]);
  const next = health(!!appRes?.ok, !!ccRes);
  if (next !== state) {
    state = next;
    tray?.setImage(trayIcon(state));
    tray?.setToolTip(healthText[state]);
    updateTrayMenu();
  }
  // The offline screen comes back to the case manager when it answers.
  if (state === "running" && mainWindow && mainWindow.webContents.getURL().startsWith("file:")) {
    void mainWindow.loadURL(cfg.appUrl);
  }
  if (state !== "running") return;
  const st = await answers(new URL("api/status", cfg.appUrl).href);
  if (!st?.ok) {
    jobs = null; // signed out
    return;
  }
  const now = snapshot((await st.json()) as JobStatus);
  const n = finished(jobs, now);
  jobs = now;
  if (n && !mainWindow?.isFocused() && Notification.isSupported()) {
    const note = new Notification(n);
    note.on("click", () => showApp());
    note.show();
  }
}

// --smoke-test=<file>: load the case manager, write what loaded and exit.
// The Windows install test uses it to prove the window trusts the local
// certificate and reaches the app.
function smokeTest(out: string): void {
  const win = createWindow(cfg.appUrl, "app");
  const finish = (ok: boolean, detail: string) => {
    writeFileSync(out, `${ok ? "OK" : "FAILED"} ${detail}\n`);
    app.exit(ok ? 0 : 1);
  };
  const timer = setTimeout(() => finish(false, `timed out at ${win.webContents.getURL()}`), 90_000);
  win.webContents.on("did-finish-load", async () => {
    const url = win.webContents.getURL();
    if (!url.startsWith(cfg.appUrl)) {
      if (url.startsWith("file:")) {
        // Offline screen: the services may still be starting; try again.
        setTimeout(() => void win.loadURL(cfg.appUrl), 3000);
      }
      return;
    }
    // Wait for the React app to render its first screen.
    for (let i = 0; i < 60; i++) {
      const text: string = await win.webContents.executeJavaScript("document.body.innerText", true).catch(() => "");
      if (text.trim()) {
        clearTimeout(timer);
        finish(true, `${url} ${JSON.stringify(text.trim().split("\n")[0])}`);
        return;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  });
}

app.on("second-instance", () => showApp());
app.on("window-all-closed", () => app.quit());
app.on("web-contents-created", (_e, wc) => {
  wc.on("will-attach-webview", (e) => e.preventDefault());
});

void app.whenReady().then(() => {
  caPem = readCA();
  lockDown(session.defaultSession);
  if (smokeOut) {
    smokeTest(smokeOut);
    return;
  }
  buildMenu();
  // First run after installing: the installer leaves the Control Center
  // address with the one-time setup code, so the Admin isn't asked for it.
  let first: string | null = null;
  try {
    first = handoffUrl(readFileSync(cfg.handoffPath, "utf8"), cfg.controlCenterUrl);
    rmSync(cfg.handoffPath, { force: true });
  } catch {
    // Not the first run.
  }
  showApp();
  if (first) showControlCenter(first);
  buildTray();
  void poll();
  setInterval(() => void poll(), 5000);
});
