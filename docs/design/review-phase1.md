# Design review: phase 1 (Control Center and setup wizard)

Date: 2026-09-29. Reviewed against the approved design brief (Impeccable is not installed; general practice applied). Screens were captured from the real launcher driving real containers, at 1440px (light and dark) and 820px.

## Fixed during review

| # | Issue | Fix |
|---|---|---|
| 1 | Rail label "System checks" wrapped because the meta text "1 to review" was too long | Meta is now a count with the warning icon |
| 2 | Services table row dividers broke under the service name (`display: grid` on a table cell) | Grid moved to an inner element |
| 3 | Rail background stopped at the viewport height on long pages | Rail is a full-height column with a sticky inner panel |
| 4 | Wizard step 2 repeated "0 of 6 running" twice, once in mono | One count, in the UI face |
| 5 | A failed "Start all" showed only in the Services strip; step 2 still said "To do" | Step 2 shows Failed with the plain-language reason and a "Try again" button |
| 6 | Docker bridge and WSL adapters (172.17.x, vEthernet) were offered as "Address to share" and triggered a false certificate warning | Virtual adapters are filtered out |
| 7 | Local AI models showed version "24.04" (the base image's Ubuntu label) | The pinned image tag wins over base-image labels |
| 8 | Activity table wrapped action names over three lines; raw compose errors ran long | Action and time columns don't wrap; details clamp to two lines with the full text on hover |
| 9 | Stopping one service re-expanded the whole setup wizard | The wizard stays collapsed once the Admin exists; the summary turns amber and names the step that needs attention |
| 10 | Wizard button read "Starting services" during a stop | Label follows the running action |

## Checked and passing

- **Hierarchy:** one page title level per section, 18px section titles, 14px body, 12px uppercase table headers.
- **State language:** every service, check and step state has a distinct icon shape, a text label and a colour. No state relies on colour alone.
- **Contrast:** all token pairs pass WCAG AA in both themes (script in `docs/design`, results in the design brief).
- **States covered:** setup code (empty, wrong code), sign-in error, loading skeleton, services not started, starting (with fixed-width timer, no layout shift), start failed, all running, service stopping, Docker not running (banner plus unknown states), no LAN address, logs empty and filtered.
- **Keyboard:** skip link, visible 2px focus ring, native `<dialog>` for confirmations (focus trap and Escape), switch has `role="switch"`, log region is focusable with `role="log"`.
- **Reduced motion:** all transitions disabled; progress uses a static icon and a counter, never a spinner.
- **Tablet (820px):** rail becomes a horizontal bar; the services table scrolls inside its frame.

## Left for later phases

- Automatic crash-restart note ("Restarted automatically at 09:14 after a crash") is covered by a unit test but wasn't captured on screen: Docker ignores signals sent to PID 1 in the test containers.
- The Control Center uses native elements instead of Radix; the main app (phase 2) will use Radix primitives as the brief says.
