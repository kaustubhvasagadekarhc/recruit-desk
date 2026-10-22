# Recruit Desk UI audit and 2026 redesign

Author: kaustubh.s.v. Audit of version 0.4.1, written 2026-10-15 by the UI 2026 temp (worker-desktop-ui-2026). The redesign shipped as 0.5.0.

## How the audit was done

- **Screens.** Every screen and state was captured into `shots/ui26-before-*.png` at 1280×800, in dark mode, and in a 1024×640 window (the app's minimum size). The capture uses a test harness: the real `dist/` front end runs in Chromium, with a fake `window.__TAURI__` backend fed by JSON exported from the real workbooks (Nordstrom has 1,501 rows). Agent output is replayed from real `progress.log` lines. No agent searches were run, and `~/.claude.json` was never touched. The harness is in `scripts/ui-harness/` (see its README). The fixture JSON is not committed, because it holds candidate data.
- **Contrast.** Ratios were calculated from the CSS tokens with the WCAG 2.x relative-luminance formula.
- **Big table.** Timed in Chromium with Nordstrom selected: 188 ms to show the sheet, 81 ms per filter keystroke and 71 ms per sort, with 17,063 DOM nodes. Every row was in the DOM. WebKitGTK, the engine used on Linux, and WebView2, used on Windows, are usually slower than headless Chromium on large DOMs, and "All columns" (14 columns, about 21k cells) multiplies the cost.
- **Background read.** BUILD-LOG's UX section, the README, and the memories of the earlier desktop temps.

Severity scale: **High** means it blocks or misleads a recruiter, or fails WCAG 2.2 AA. **Medium** means a clear usability or polish gap. **Low** means nice to have.

## Findings (0.4.1)

| # | Component | Problem | Category | Severity | Fixed in 0.5.0 |
|---|-----------|---------|----------|----------|----------------|
| 1 | Run panel (both sections) | After a run, the result card and the step list push the form and the Run button out of view (`run-card` scrolls as one block). See before-09 and before-19: the "Check jobs" button is cut off at the top. | layout, hierarchy | High | Yes: the form stays fixed and only the progress area scrolls |
| 2 | Step checklist | Step result lines are raw agent output: "6 rows in, 0 duplicates dropped, 0 new, 6 updated, 0 closed, 0 stale; company total 10 (4 open, 5 unverified…)", "memory has same name under humancloud.dev; careers page unknown, job board none", "JD 'senior-react-frontend-pune' registered". This is the open item from 0.2.0. | wording | High | Yes: a per-step plain-language translator (`src/plain.ts`, with tests) |
| 3 | Excel viewer, big sheet | All 1,501 Nordstrom rows are rendered (17k nodes). Filter and sort rebuild the whole table on every keystroke, with no debounce. | performance | High | Yes: virtualised rows (about 40 in the DOM), debounced filter |
| 4 | Excel viewer at 1024 px | The toolbar overflows. "Open in Excel" is clipped off the card, Refresh wraps onto its own line, and the fourth column is cut (before-21). | layout | High | Yes: the toolbar wraps on a grid, and the table uses fixed column widths with ellipsis |
| 5 | Lock overlay | `aria-modal="false"`, focus is not moved into it, Tab reaches the blurred controls behind it, and Esc does nothing. | keyboard and focus | High | Yes: focus moves to the button, Tab is trapped, Esc shows the saved results |
| 6 | Text inputs, select, table frame | Border `--line` is 1.28–1.37:1 against its background. WCAG 1.4.11 needs 3:1 for a component's boundary. | contrast | High | Yes: new `--field-line` token (light 3.3–3.5:1, dark 3.3–3.8:1) |
| 7 | Excel viewer rows | Two-line clamped cells and an "Open" pill in every row: about 8 rows show at 800 px. Long titles are cut mid-word ("Pune, Maharashtr…"). | density | Medium | Yes: single-line 40 px rows, a full value on hover, details on click (about 13 rows show) |
| 8 | Excel viewer columns | Columns cannot be resized, so widths jump between sheets and when "All columns" is toggled. | data table | Medium | Yes: drag or keyboard (arrow keys) column resize, widths remembered per sheet |
| 9 | Usage meter | The real numbers are only in a hover tooltip. The box has `tabindex=0` and `cursor: help` but does nothing. "Usage this period" is vague. | wording, a11y | Medium | Yes: shows "Allowance used" and "2.6M of 3M tokens" in place; meter semantics; not focusable |
| 10 | Usage near the cap | At 80–99% the only cue is an amber bar and "Running low". Nothing appears near the Run button, where the decision is made. | hierarchy | Medium | Yes: a warning line above Run at 80% or more |
| 11 | Sidebar status dots | Running and on-a-break states are shown by colour alone (green or amber dot). | a11y (1.4.1) | Medium | Yes: dot plus text ("Working", "On a break") |
| 12 | Error result | It tells the person to "open Show details" but gives no direct action. There is no "Try again" button. | error state | Medium | Yes: Try again and Show details buttons; Setup check when the cause is trust |
| 13 | Running state | No elapsed time and no reassurance during long steps. Stop is a plain grey button with no confirmation. | loading state | Medium | Yes: an elapsed timer, and Stop asks for confirmation |
| 14 | Toasts | A single bottom-centre toast that covers the table, auto-hides after 5 s even for errors, and has no close button. Errors use `role=status`. | feedback | Medium | Yes: stacked bottom-right; success hides itself, errors stay until closed (`role=alert`); a close button |
| 15 | Search with no matches | Shown as a row inside the table, with no one-click way to clear it. | empty state | Medium | Yes: an empty panel with a "Clear search" button |
| 16 | Empty workbook | Plain centred text that repeats the left panel. | empty state | Low | Yes: an icon, a short line, and a button that focuses the input |
| 17 | Keyboard shortcuts | Only Ctrl+Enter in the job description box. No way to jump between sections or to the search box. | keyboard | Medium | Yes: Ctrl+1/2/3, Ctrl+F, Ctrl+Enter in both forms, Ctrl+K quick actions |
| 18 | Command palette | None. Common actions (Open in Excel, refresh, setup check, theme) take several clicks across views. | navigation | Low | Yes: Ctrl+K quick actions, also a button in the sidebar |
| 19 | Placeholder text | Muted at 85% opacity: about 4.4:1, below 4.5:1. | contrast | Medium | Yes: full-opacity `--muted` (5.9:1) |
| 20 | Motion | Only the progress bar respects reduced motion. The spinner still spins at reduced motion, and there are no view or overlay transitions. | motion | Low | Yes: short fades and lifts, all removed under `prefers-reduced-motion` |
| 21 | Setup check | Good overall. There is no "x of 7 ready" summary, and finished items take a full card each. | hierarchy | Low | Yes: a progress line and compact ready rows |
| 22 | Settings, allowance | Plain number input ("3000000") with no sense of scale. | wording | Low | Yes: a hint with roughly how many checks the allowance covers |
| 23 | Row details dialog | Fine (native `<dialog>`, Esc works). The Close button is a ghost link. | consistency | Low | Restyled |
| 24 | Theme | Dark theme is solid. Card shadows vanish in dark and cards blend into the background (1.37:1 outline). | theming | Low | Yes: dark cards get a lighter surface step |
| 25 | Credit | "Built by kaustubh.s.v" is present in the sidebar and in About. | — | OK | Kept |

## Result in 0.5.0 (same harness)

- **Big table.** With Nordstrom (1,501 rows), 23 rows are in the DOM instead of 1,501, and the page has 804 nodes instead of 17,063. The sheet shows in 51–66 ms instead of 188 ms. The filter is debounced by 120 ms and searches a lower-cased index built once per sheet.
- **Real app.** The Linux build was checked in real WebKitGTK (`shots/ui26-after-30-real-webkitgtk.png`): the setup screen rendered correctly and matched the harness. Driving it further with XTest did not register on this desktop, so the other "after" states come from the harness only.
- **Tests.** `npm run test:ui`: 17 plain-language checks. `cargo test --lib`: 44, including 4 new static UI checks in `src-tauri/src/ui_tests.rs`.

## Research: how 2026 desktop productivity apps build UI

1. **Navigation density and calm chrome.** Linear's redesign cut visual noise in the sidebar, tabs and headers, and its later refresh moved to warmer, less saturated neutrals while keeping density. Recruit Desk keeps its pine accent, but uses quieter neutrals and lets content carry the weight. [Linear: How we redesigned the Linear UI](https://linear.app/blog/how-we-redesigned-the-linear-ui) · [Linear: Behind the latest design refresh](https://linear.app/blog/behind-the-latest-design-refresh)
2. **Perceptual colour for themes.** Linear generates themes in LCH, so one lightness value reads equally bright across hues. Here, light and dark tokens were tuned so the status chips have matching contrast (all 4.5:1 or better). [Linear redesign post](https://linear.app/blog/how-we-redesigned-the-linear-ui)
3. **Keyboard first, with discoverable shortcuts.** Raycast is built to be driven entirely from the keyboard and shows each action's shortcut next to it. Linear does the same in its command menu. This led to Ctrl+K quick actions that list their shortcuts. [Raycast manual: keyboard shortcuts](https://manual.raycast.com/keyboard-shortcuts.md) · [Raycast: A fresh look and feel](https://www.raycast.com/blog/a-fresh-look-and-feel)
4. **Toasts belong to the window and can be expanded or dismissed.** Raycast moved toasts into the app window. Here, toasts are stacked in a corner, errors stay until closed, and they never cover the primary action. [Raycast: A fresh look and feel](https://www.raycast.com/blog/a-fresh-look-and-feel)
5. **Density options for information-rich views.** Microsoft's Windows guidance pairs Standard (40 epx targets) with Compact for "browsing large amounts of content", with a 14 px body. The table uses compact 40 px rows with 14 px text, while forms keep standard sizing. [Microsoft Learn: Spacing and density](https://learn.microsoft.com/en-us/windows/apps/design/style/spacing)
6. **Virtualised grids must still announce their real size.** Use `aria-rowcount` on the table and `aria-rowindex` on each rendered row, so screen readers report "row 51 of 1,501". [W3C ARIA APG: Grid and table properties](https://www.w3.org/WAI/ARIA/apg/practices/grid-and-table-properties/) · [MDN: aria-rowindextext](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Attributes/aria-rowindextext)
7. **WCAG 2.2 focus rules.** Focus must not be hidden by sticky headers, footers or overlays (2.4.11). Component boundaries and focus rings need 3:1 against adjacent colours (1.4.11). The redesign adds scroll padding under the sticky table header and setup footer, and stronger field borders. [TetraLogical: What's new in WCAG 2.2](https://tetralogical.com/blog/2023/10/05/whats-new-wcag-2.2)
8. **Target size.** WCAG 2.2 adds 2.5.8 Target Size (Minimum, AA). Row actions, chips that act as buttons, and icon buttons are kept at 32 px or more. [TetraLogical: What's new in WCAG 2.2](https://tetralogical.com/blog/2023/10/05/whats-new-wcag-2.2)
9. **Reduce motion as a first-class setting.** Apple's guidance is to build Reduce Motion in from the start and to swap movement for simple fades. All transitions here are 120–200 ms fades or small lifts, and they are removed under `prefers-reduced-motion`. [Apple HIG: Motion](https://developer.apple.com/design/human-interface-guidelines/motion) · [Apple HIG: Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)
10. **Sidebar plus toolbar.** A leading sidebar for places, and a toolbar for the actions on the current content. The Excel viewer's controls became one toolbar row (picker, search, columns, refresh, Open in Excel) that wraps cleanly. [Apple HIG: Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars) · [Apple HIG: Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars)
11. **Empty states teach the next step.** A short line plus one action that does the next thing (focus the input, clear the search) instead of prose that repeats the form. This pattern is common to Linear, Notion and Fluent 2. [Fluent 2 design system](https://fluent2.microsoft.design/)
12. **Plain language over system output.** For non-technical users, translate machine logs into outcomes ("Found 6 jobs on the careers page") and keep the raw log one click away under Show details. This applies the HIG's writing guidance. [Apple HIG: Writing](https://developer.apple.com/design/human-interface-guidelines/writing)

Note: some search results were third-party summaries of Apple's HIG and Fluent 2. The links above point to the official pages.

## Design direction for 0.5.0

The look is a quiet, dense working surface: a recruiter's desk, not a dashboard.

- **Kept from the earlier pass.** The pine identity, the step checklist (the one bold element), plain wording, and the system font stack. The CSP is `'self'`, so the app uses no web fonts.
- **Structure.** The sidebar holds the places, plus a quick-actions entry and the credit. Each section has a header with the allowance meter, a fixed run panel (form at top, scrollable progress below), and a results panel with one toolbar and a virtualised table.
- **Library choice.** No library was added. Virtualisation with fixed 40 px rows is about 60 lines of TypeScript, which is smaller and easier to audit than a dependency.
