---
name: Case File Manager
description: A quiet white and rose workspace for document review and topic agents.
colors:
  accent: "#b83d62"
  accent-dark: "#9e2d50"
  rose: "#fbeaf0"
  background: "#fff"
  sidebar: "#faf9fa"
  subtle: "#f8f7f8"
  text: "#25232b"
  muted: "#69636c"
  line: "#e9e5e8"
  control-text: "#403b44"
  hover-surface: "#f1edef"
  button-hover: "#f8f5f7"
  button-hover-border: "#d6cbd3"
  panel-hover: "#fcf8fa"
  panel-hover-border: "#e3c2ce"
  emphasis-border: "#c87692"
  blue-bg: "#eaf3fb"
  blue-text: "#476880"
  amber-bg: "#faf2dc"
  amber-text: "#806725"
  mint-bg: "#e7f5ee"
  mint-text: "#397c60"
  lavender-bg: "#f0ebf8"
  lavender-text: "#716091"
  success: "#28644d"
  success-bg: "#edf7f1"
  error: "#a52d42"
  error-bg: "#fff0f2"
typography:
  display:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-.035em"
  headline:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.3
    letterSpacing: "-.035em"
  title:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "-.015em"
  body:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "14px"
    fontWeight: 400
  message:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  badge: "4px"
  navigation: "7px"
  control: "8px"
  compact-panel: "10px"
  panel: "12px"
  composer: "16px"
spacing:
  micro: "4px"
  tight: "6px"
  compact: "8px"
  control: "10px"
  group: "12px"
  inset: "16px"
  row: "20px"
  section: "24px"
  desktop-gutter: "40px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.background}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-primary-hover:
    backgroundColor: "{colors.accent-dark}"
  button-secondary:
    backgroundColor: "{colors.background}"
    textColor: "{colors.control-text}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-secondary-hover:
    backgroundColor: "{colors.button-hover}"
  button-subtle:
    backgroundColor: "{colors.background}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    rounded: "{rounded.control}"
    padding: "10px 14px"
  button-subtle-hover:
    backgroundColor: "{colors.subtle}"
  input:
    backgroundColor: "{colors.background}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "11px 12px"
  navigation-active:
    backgroundColor: "{colors.rose}"
    textColor: "{colors.accent}"
    rounded: "{rounded.navigation}"
    padding: "12px 13px"
  quick-start:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.panel}"
    padding: "18px 16px"
  composer:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.composer}"
    padding: "16px 18px 12px"
  provider-choice:
    backgroundColor: "{colors.background}"
    rounded: "{rounded.compact-panel}"
    padding: "19px"
  keyword-tag:
    backgroundColor: "#f5f1f5"
    textColor: "#756879"
    rounded: "{rounded.badge}"
    padding: "3px 7px"
  citation:
    backgroundColor: "#fcf2f6"
    textColor: "{colors.accent-dark}"
    padding: "6px 8px"
---

# Design System: Case File Manager

## Overview

The confirmed visual reference is the user's TencentCloud/Octop desktop chat screenshot. Preserve its quiet white workspace, pale sidebar, restrained rose selection, outline icons and generous composer under the Case File Manager identity. The implementation carries that language through document tables, specialist agent rows, review tasks and provider selection.

The visual system is compact and practical: fine borders establish structure, muted labels support the content, and rose draws attention to selection and primary actions. No new creative metaphor has been selected. This document records the implemented system rather than proposing another visual direction; page strategy remains in `docs/design/workspace.md`.

**Key Characteristics:**

- White content with a pale sidebar and restrained rose actions.
- System sans typography and consistent outline icons.
- Border-defined rows and panels with softly curved controls.
- Source passages and external consent presented in focused dialogs.

Source evidence: `control-center/src/style.css` and `control-center/src/App.tsx`. These tokens describe source styles; they are not claims of browser-computed sampling or design-tool validation. Frontmatter owns reusable primitives; `.impeccable/design.json` supplies preview snippets and extensions. Its generated tonal ramps are preview aids, not additional application palette commitments.

## Colors

### Primary

The muted rose accent identifies primary actions, active navigation, links, focus and the application mark. Its darker companion supports hover and citation text; pale rose provides the selected navigation fill. Use the frontmatter's `accent`, `accent-dark` and `rose` values without creating a second palette.

**The Rose Selection Rule.** Keep primary action and selection treatments in the established rose family across views.

### Secondary

Blue, amber, mint and lavender pairs distinguish quick-start and topic-agent icon tiles. They remain supporting identifiers rather than alternate primary-action colors. Provider monograms have their own source-defined identity tints; preserve those within provider selection rather than promoting them into global accents.

### Neutral

White is the content and control surface. The pale sidebar and subtle surface separate navigation, toolbar and consent context. Dark text carries headings and content; muted text carries labels, dates and explanations; the fine line color divides tables, forms and panels. Hover surfaces and emphasis borders provide small state changes without changing the white workspace.

Success and failure use green and red text with pale fills in task status. Alerts use their source-specific variants and always retain visible text and icons.

## Typography

The display and body share the system sans stack declared in the frontmatter. There is no separate display face, downloaded font or general-purpose monospace role. Inline server-setting identifiers use native code styling.

The desktop welcome heading uses the display role; page headings use headline; section headings use title. The root body size is the baseline, while chat content uses the message role and buttons use label. Supporting metadata is deliberately smaller, generally (9–11px), and document or agent labels generally use (12–14px). Counts, document sizes and dates use tabular figures where the source specifies them.

Chat paragraphs and findings constrain readable text to (75ch); agent descriptions use (65ch). Welcome headings reduce to (27px) at the middle breakpoint and (25px) on mobile; mobile page headings use (24px). Preserve sentence case and visible action labels.

## Layout

The desktop shell has a left navigation column (224px), a top bar (70px) and a fluid content column. Standard pages center within a maximum width (1200px) with desktop horizontal padding from `desktop-gutter`; model and settings pages cap at (1000px). Chat centers within (820px) and keeps the composer at the bottom of its flex layout. Quick starts use a two-column grid.

At a viewport width of (1100px) or less, the sidebar narrows to (200px), page gutters become (26px), and the document date column is hidden. At (760px) or less, the shell becomes one column with a top bar (62px), case switcher and five-item horizontal navigation strip. Page gutters become (18px); document size and date metadata are hidden, while file names, selection and source actions remain. Agent actions wrap beneath the description, form fields stack, and provider status moves onto a supporting line. Quick starts retain two columns with smaller insets.

At (1450px) or wider, chat expands to a maximum width (900px). These are observed breakpoints, not a new spacing framework. The reusable spacing entries reflect existing gaps and insets; individual rows retain source-specific measurements.

## Elevation & Depth

The workspace is flat by default and derives structure from fine borders, pale surfaces and spacing. The composer has a faint resting shadow; dialogs and the upload toast have stronger elevation. Modal backdrops dim the workspace while keeping source and consent content legible.

Exact composer, dialog and toast shadows are recorded in the sidecar. Control color transitions last (160ms); source-dialog entry uses a short fade and upward reveal over the same interval. Loading indicators rotate. The reduced-motion query nearly eliminates transitions and animations and limits animation iteration.

**The Border-First Rule.** Preserve row dividers and tonal grouping for ordinary content; keep elevated shadows on the composer, dialogs and transient upload feedback.

## Shapes

Small badges use the compact badge radius; navigation and icon buttons have slightly curved corners. Inputs and standard buttons use the control radius, compact grouping panels use compact-panel, and quick starts and upload panels use panel. The composer and dialogs use the larger composer radius; the mobile composer tightens to (13px). Avatars and attachment controls are circular.

Lucide outline icons use the global stroke width (1.7), with source-specific mark overrides. Reuse the existing geometric application mark and provider monograms. The implementation contains no raster artwork or photographic surfaces.

## Components

### Buttons

Primary buttons have rose fill, white text and a darker hover. Secondary buttons have a white fill, neutral border and a lightly tinted hover; subtle buttons omit the visible resting border. All share label typography and the control shape. Icon buttons use muted outline icons and a pale hover. Keyboard focus is a rose outline (2px) with an offset (3px); disabled buttons retain the source opacity (.48). The send button has its own pale rose disabled treatment.

### Chips

Keyword tags are compact, passive labels with a pale mauve fill, rounded badge corners and small muted text. Status badges pair visible status words with semantic tinting. These are not interchangeable with selectable filter controls.

### Cards / Containers

Quick starts are white, border-defined panels with a supporting icon tile, short title and muted description; hover changes the fill and border. Review controls use a subtle grouping surface. Document, agent and task collections use rows and dividers rather than a grid of elevated cards. Upload panels retain the dashed border and explicit browse action; drag state uses rose selection.

### Inputs / Fields

Fields use white fill, fine neutral borders, control corners and the frontmatter inset. Focus changes the border to rose and retains the global keyboard outline. Search wraps an outline search icon and an unbordered inner input within one bordered field. The composer has a larger rose-tinted border and border change on focus within; its textarea is visually integrated and retains keyboard focus styling.

### Navigation

Desktop navigation is a vertical list on the pale sidebar, with outline icons, visible labels and optional counts. The current item uses pale rose fill and rose text. Hover uses a pale neutral fill. On mobile the same five main destinations become a horizontal strip; counts and the secondary sidebar content are hidden.

### Evidence and Consent

Citation buttons place the source name and location together and open the source-document dialog. The dialog has a header, original-file action, scrollable extracted text and a pale highlighted cited passage. The external-analysis consent dialog presents provider, model, scope and documents with paired local and approve actions. Both retain the shared dialog shape, backdrop and accessible close controls.

### Topic Agents and Providers

Topic agents are selectable rows with colored outline-icon tiles, topic instructions, keyword tags and explicit edit/run actions. Provider choices use native radio controls, compact monograms, provider descriptions and key status; selected rows use the emphasis border and a light rose-tinted fill. Preserve the model selection form and its separate server-configuration explanation.

## Do's and Don'ts

### Do:

- **Do** preserve the confirmed Octop-inspired white workspace, pale sidebar and rose selection under the Case File Manager identity.
- **Do** use the existing system sans hierarchy, outline icons and visible action labels.
- **Do** keep source names, locations and cited passages visually connected.
- **Do** use row dividers and subtle surfaces for dense document, agent and task collections.
- **Do** preserve keyboard focus, reduced-motion behavior and text alongside semantic status colors.

### Don't:

- **Don't** reuse Octop's mascot or branding in place of the Case File Manager identity.
- **Don't** replace the restrained rose action palette with topic-agent or provider identity colors.
- **Don't** add large shadows or decorative art to ordinary table and list surfaces.
- **Don't** hide source or consent actions behind unlabeled icons when their existing text labels explain the action.
