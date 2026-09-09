# Browser and accessibility verification

Run this lens when the change affects a browser surface or its behaviour. Read the
project's supported browsers/devices, browser configuration and relevant usage data.
Record exact targets; do not substitute an unrelated team's last-two-versions policy.
Use [MDN compatibility data](https://developer.mozilla.org/en-US/docs/MDN/Writing_guidelines/Page_structures/Compatibility_tables)
and [Baseline](https://web.dev/baseline) to identify risk, then exercise the fallback
in the target environment. A feature guard, try/catch or presumed transpiler/polyfill
does not prove that an unsupported path remains usable. Inspect the actual build setup.

Default to [WCAG 2.2 AA](https://www.w3.org/TR/WCAG22/) unless the project specifies
another target. Record the target and test result per affected interaction. Check:

- Text contrast at least 4.5:1, or 3:1 for large text; applicable non-text contrast 3:1.
- Keyboard navigation, visible focus, no keyboard trap, modal focus entry and return,
  and focus not entirely obscured by author-created content.
- Accessible names, native semantics, labels and error associations; meaningful async
  status/error announcements without announcing every incidental update.
- Zoom/reflow, orientation and mobile layout; loading, empty, error and recovery states.
- Applicable target-size requirements (24 by 24 CSS pixels or a permitted exception),
  non-drag alternatives, redundant entry and accessible authentication.
- Motion preferences, pausable moving content and flash thresholds. Check the precise
  criterion and exceptions; do not label every animation or colour choice a violation.

Use configured automated accessibility checks plus targeted keyboard, interaction and
assistive-technology checks. Record browser/version, viewport, URL or fixture, steps,
expected/actual behaviour and evidence. A screenshot cannot establish keyboard or
screen-reader behaviour. An automated pass does not certify WCAG conformance.

Block a verified violation of the agreed target or a required journey that is unusable.
An untested required target is unverified, not approved. Lower-severity preferences
need a concrete benefit and must not become mandatory refactors. Do not run browser
checks for non-UI changes without a relevant dependency or requirement.
