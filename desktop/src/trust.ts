// The app trusts exactly one certificate authority: the local CA Setup.exe
// created on this computer. That removes the browser's certificate warning
// here without trusting the CA anywhere else in Windows.
import { X509Certificate } from "node:crypto";

export type Verdict = { ok: true } | { ok: false; reason: string };

const LOCAL = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);

export function verifyServerCertificate(leafPem: string, caPem: string, hostname: string, now = new Date()): Verdict {
  if (!LOCAL.has(hostname)) return { ok: false, reason: `not this computer: ${hostname}` };
  let leaf: X509Certificate, ca: X509Certificate;
  try {
    leaf = new X509Certificate(leafPem);
    ca = new X509Certificate(caPem);
  } catch {
    return { ok: false, reason: "unreadable certificate" };
  }
  if (!ca.ca) return { ok: false, reason: "the local CA file isn't a certificate authority" };
  if (!leaf.checkIssued(ca) || !leaf.verify(ca.publicKey)) return { ok: false, reason: "not issued by this computer's Case File Manager CA" };
  if (now < new Date(leaf.validFrom) || now > new Date(leaf.validTo)) return { ok: false, reason: "the certificate has expired; renew it in the Control Center" };
  const host = hostname.replace(/^\[|\]$/g, "");
  const matches = host.includes(":") || /^\d+\.\d+\.\d+\.\d+$/.test(host) ? leaf.checkIP(host) : leaf.checkHost(host);
  if (!matches) return { ok: false, reason: `the certificate doesn't name ${hostname}` };
  return { ok: true };
}
