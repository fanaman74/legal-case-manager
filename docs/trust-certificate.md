# Trusting the Case File Manager certificate

The app uses HTTPS with its own local certificate authority (CA), created on install. Browsers warn about it until each device trusts that CA once. The CA stays the same when the certificate is renewed, so you only do this once per device.

Get the CA file from **Control Center › Network › Download certificate authority** (`case-file-manager-ca.crt`). It contains no secret; it's safe to email or copy to other devices on a USB stick.

## Windows

1. Double-click `case-file-manager-ca.crt`, then **Install Certificate**.
2. Choose **Local Machine**, then **Place all certificates in the following store** › **Trusted Root Certification Authorities**.
3. Finish, then restart the browser.

## macOS

1. Double-click the file. Keychain Access opens; add it to the **System** keychain.
2. Find *Case File Manager local CA*, double-click it, open **Trust**, set **When using this certificate** to **Always Trust**.

## iPad and iPhone

1. Send the file to the device (AirDrop or email) and open it. Tap **Allow**, then install the profile in **Settings › Profile Downloaded**.
2. Go to **Settings › General › About › Certificate Trust Settings** and turn on *Case File Manager local CA*.

## Android

**Settings › Security › Encryption & credentials › Install a certificate › CA certificate**, then choose the file.

## Firefox

Firefox has its own list: **Settings › Privacy & Security › Certificates › View Certificates › Authorities › Import**, and tick *Trust this CA to identify websites*.

## If the address changes

The certificate names this computer's network address. If the address changes (for example after a router restart), the Control Center shows a warning. Renew the certificate there, and reserve the address in your router so it stays the same.
