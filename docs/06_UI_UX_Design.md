# UI/UX Design Document

**Project Name:** LastPing
**Document Version:** 1.1
**Author:** Jose Medina
**Last Updated:** July 2026

---

## 1. Introduction

This document defines the user interface and user experience design for LastPing. It translates the functional requirements around the Desktop Application (FR-035 through FR-042) and the personas defined in the SRS into concrete screens, navigation flows, and visual design decisions. It is meant to guide PySide6 implementation and to keep the interface consistent as new screens are added.

## 2. Design Goals

- **Clarity over density**: a user should be able to tell a service's health at a glance without reading text.
- **Low friction for common tasks**: adding a service and reading recent history should each take only a few clicks.
- **Consistency**: the same status vocabulary and color coding must appear identically on the dashboard, the detail view, and in alerts.
- **Respect for the single-user, technical audience**: the UI can assume familiarity with terms like "TCP," "DNS," and "response time," per the personas in the SRS (Daniel, Priya, Marcus, Elena), without over-explaining.
- **Non-blocking feedback**: monitoring activity happening in the background must never freeze or visibly stall the interface.

## 3. Design Principles

Beyond the project-specific goals above, LastPing's interface follows a set of general UX principles that should guide any new screen or component added later.

- **Visual hierarchy**: the most important information (current status, uptime percentage) is the largest and most prominent element on any screen; supporting detail (exact timestamps, retry counts) is smaller and secondary.
- **Recognition over recall**: the user should never need to remember a service's configuration, threshold, or last-known state. Everything relevant to a decision is shown on screen rather than requiring the user to hold it in memory (e.g., the alert threshold is visible in the service detail view, not just buried in a settings screen the user configured once and forgot).
- **Progressive disclosure**: the Dashboard shows only what's needed to assess health at a glance; deeper detail (raw check history, exact failure reasons, per-check latency) is available a click away in Service Detail, not shown by default.
- **Consistency**: identical status vocabulary, color coding, and iconography across the Dashboard, Service Detail, Incidents, and Alert History screens (Section 8).
- **Feedback**: every user action (save, delete, pause, manual check) produces an immediate, visible acknowledgment — see Loading States (Section 10).
- **Error prevention**: destructive or misconfigurable actions are guarded before they happen (inline validation on the Add/Edit dialog, confirmation before delete) rather than relying on the user to notice a mistake after the fact.

## 4. Information Architecture

LastPing's UI is organized around five primary screens, reachable through a persistent left-hand navigation rail:

```
┌───────────────┐
│  Navigation    │
│  ───────────   │
│  ▸ Dashboard   │
│  ▸ Services    │
│  ▸ Incidents   │
│  ▸ Alert History│
│  ▸ Settings    │
└───────────────┘
```

- **Dashboard**: overview of all services, current status.
- **Services**: management list, add/edit/delete/pause.
- **Incidents**: cross-service incident history and filtering (supports FR-023).
- **Alert History**: history of dispatched notifications (supports FR-033).
- **Settings**: global defaults, SMTP, Discord, database, logging (FR-043–FR-047).

Selecting a service from the Dashboard or Services list opens its **Service Detail** view, which is a drill-down rather than a separate top-level nav item.

> **Terminology note**: this screen is deliberately named "Alert History" rather than "Alerts Log." "Log" reads as developer-facing; "History" reads as a user-facing record, which better matches the audience for this screen (all personas, not just Marcus the student who might enjoy the word "log").

## 5. Screen Designs

### Legend

The following symbols are used consistently across every wireframe in this document and in the implemented UI:

```
●  Healthy
▲  Degraded
✖  Down
⏸  Paused
```

### 5.1 Dashboard

```
┌──────────────────────────────────────────────────────────────┐
│  LastPing                                    [ + Add Service ]│
├──────────────────────────────────────────────────────────────┤
│  🔍 Search services...                       [All ▾][Status ▾]│
├──────────────────────────────────────────────────────────────┤
│  ● api.example.com          HTTPS   142ms   Up 99.8%         │
│  ● internal-db:5432          TCP    —       Up 100%          │
│  ▲ minecraft.myhome.net      TCP    —       Degraded         │
│  ✖ old-service.local         DNS    —       Down (12m)       │
│  ⏸ staging-api.example.com   HTTPS  —       Paused           │
└──────────────────────────────────────────────────────────────┘
```

- Each row is a **Service Card** with: status symbol, name/target, protocol badge, last response time, uptime percentage over a rolling window.
- Status colors: green (healthy), amber (degraded), red (down), gray (paused) — always paired with the Legend symbol above, never color alone.
- Clicking a card opens Service Detail. Right-click (or an overflow menu) exposes pause/resume, edit, and delete, per FR-039/FR-040.
- The search/filter bar supports FR-041 (filter by name, protocol, or status).
- See Section 12 (Empty States) for the zero-services case, and Section 14 (Responsiveness at Scale) for behavior at 50 services.

### 5.2 Service Detail

```
┌──────────────────────────────────────────────────────────────┐
│  ← Back      api.example.com (HTTPS)          [Edit] [Pause]  │
├──────────────────────────────────────────────────────────────┤
│  Uptime: 99.8%     Avg Response: 138ms     Failures (7d): 2   │
├──────────────────────────────────────────────────────────────┤
│  [ 24h ] [ 7d ] [ 30d ] [ Custom range ]                       │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │        Response Time Trend (PyQtGraph line chart)         │ │
│  └──────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│  Incident History                                              │
│  ✖ Jul 26, 3:14 AM – 3:19 AM   Connection timeout              │
│  ✖ Jul 22, 11:02 PM – 11:03 PM  DNS resolution failure          │
└──────────────────────────────────────────────────────────────┘
```

- Time-range tabs directly implement FR-025 (custom date filtering).
- The chart area is the primary implementation target for FR-022 and NFR-002/NFR-003 (must render within 3 seconds for a 90-day range).
- Incident History rows implement FR-023 and link into UC-03 (Investigate a Service Incident) from the SRS.
- While the chart query is running, this area shows a skeleton placeholder rather than a blank panel — see Section 10 (Loading States).

### 5.3 Add / Edit Service Dialog

```
┌───────────────────────────────┐
│ Add Monitored Service          │
├───────────────────────────────┤
│ Name:      [__________]        │
│ Type:      [HTTP ▾]             │
│ Target:    [__________]        │
│ Interval:  [60] seconds         │
│ Timeout:   [5] seconds          │
│ Retries:   [2]                  │
│                                 │
│ Alerts:  [ ] Desktop            │
│          [ ] Email              │
│          [ ] Discord            │
│ Threshold: [3] consecutive fails│
│                                 │
│        [Cancel]     [Save]      │
└───────────────────────────────┘
```

- A single modal handles both add and edit (FR-038, FR-039), pre-populated when editing.
- Inline validation (e.g., target format per selected type) happens before Save is enabled, satisfying NFR-007 (no external docs needed to succeed) and the "error prevention" principle in Section 3.
- The Save button disables and shows a brief in-progress state while the write completes — see Section 10.

### 5.4 Incidents Screen

A cross-service table of every recorded incident, sortable by service, duration, and time, with the same date-range filtering pattern as Service Detail. This is the aggregate view supporting Priya's postmortem workflow (US-003). See Section 12 for its empty state.

### 5.5 Alert History Screen

A read-only, reverse-chronological table of every dispatched alert: timestamp, service, channel, and outcome (delivered / failed). Directly implements FR-033. See Section 12 for its empty state and Section 13 for failed-delivery presentation.

### 5.6 Settings Screen

Organized as a tabbed panel:

- **General**: default interval, default retry count, data retention period (FR-043, FR-048).
- **Notifications**: SMTP configuration, Discord webhook URL (FR-044, FR-045).
- **Database**: connection settings (FR-046).
- **Logging**: verbosity level, log file location (FR-047).
- **Appearance**: light/dark mode toggle (FR-042).

## 6. Screen Flows

Beyond the static navigation map (Section 7), it's worth describing the common end-to-end workflows a user actually performs, since these are what the UI needs to support smoothly rather than just make reachable.

**New User Flow**

```
Launch
  ↓
Dashboard (empty state)
  ↓
Add Service
  ↓
Scheduler starts checking the new service
  ↓
Dashboard updates in place (status symbol appears)
```

**Outage Investigation Flow**

```
Dashboard (sees a ✖ Down card)
  ↓
Click service
  ↓
Service Detail → Incident History
  ↓
Select the incident's time range on the trend chart
  ↓
Export CSV (optional, for a postmortem doc)
```

**Alert Configuration Flow**

```
Add or Edit Service dialog
  ↓
Enable a notification channel + set threshold
  ↓
Save
  ↓
(later) Threshold breached → notification fires → entry appears in Alert History
```

## 7. Navigation Flow

```
Dashboard ──▶ Service Detail ──▶ Edit Service Dialog
    │              │
    │              └──▶ Incident row ──▶ (scrolls chart to incident window)
    │
    ├──▶ Add Service Dialog
    ├──▶ Incidents (global)
    ├──▶ Alert History
    └──▶ Settings
```

Every screen reachable from the nav rail is a single click from any other, keeping the app shallow (no more than two levels deep from the Dashboard), consistent with the "Simplicity" design principle in the Project Vision.

## 8. UI State Model

Each monitored service moves through a small, well-defined set of states, and the UI's job is to represent this state identically everywhere it appears (Dashboard card, Service Detail header, Incidents row).

```
        ┌──────────┐
        │ Unknown   │  (just added, no check has completed yet)
        └────┬─────┘
             │ first check completes
             ▼
        ┌──────────┐
   ┌───▶│ Healthy   │◀───┐
   │    └────┬─────┘    │
   │         │ degraded  │ recovers
   │         ▼           │
   │    ┌──────────┐     │
   │    │ Degraded  │─────┘
   │    └────┬─────┘
   │         │ crosses failure threshold
   │         ▼
   │    ┌──────────┐
   │    │ Down      │
   │    └────┬─────┘
   │         │ next check succeeds
   │         ▼
   │    ┌──────────┐
   └────│ Recovering │  (shown briefly, then settles to Healthy)
        └──────────┘

Paused can be entered from, and exited back to, any of the states above,
and does not itself trigger alerts (FR-034 is the related mute/snooze behavior).
```

This state model is what the Dashboard's status symbol, the Service Detail header, and the Alerting Engine's threshold evaluation (Architecture doc, Section 4.4) are all reflecting from three different angles — so a change to one (e.g., adding a new state) should be checked against all three.

## 9. Visual Design System

| Element | Treatment |
|---|---|
| Status color — Healthy | Green |
| Status color — Degraded | Amber |
| Status color — Down | Red |
| Status color — Paused | Gray |
| Typography | A single system-appropriate sans-serif font family; monospace used only for raw values like status codes and IPs |
| Charts | Line charts for response time trend; a lighter shaded band to indicate degraded threshold zone |
| Spacing | Card-based layout with consistent padding so the dashboard remains scannable as the service count grows toward the 50-service target (NFR-001) |
| Iconography | Minimal, functional icons only (pause, edit, delete, search) — no decorative iconography |

## 10. Loading States

Because monitoring runs asynchronously in the background (per the Architecture document's concurrency model), the UI must never present a blank or ambiguous screen while waiting on data.

- **Charts** display a skeleton placeholder (a grayed-out outline of the chart axes) while a trend-chart query is in flight, rather than a blank panel.
- **Lists** (Dashboard, Incidents, Alert History) show a lightweight loading indicator on first load, and update in place afterward via the live status event stream rather than reloading the whole list.
- **Buttons** that trigger a write (Save, Delete, manual "Check Now") disable and show a brief in-progress state until the operation completes, preventing duplicate submissions.
- **Background operations never block the UI**: because monitoring checks and database writes happen off the Qt main thread via the `qasync` bridge, the interface remains interactive at all times, including while a check for a slow or unresponsive service is still pending.

## 11. Empty States

Every list-based screen needs an explicit, helpful empty state rather than a bare "No data" message.

**Dashboard, zero services:**
```
No services are currently being monitored.

              [ + Add Service ]
```

**Incidents, no incidents recorded:**
```
No incidents yet. Once a monitored service experiences
a failure, it will show up here.
```

**Alert History, no alerts sent:**
```
No alerts have been sent yet. Alerts appear here once a
service crosses its configured failure threshold.
```

**Service Detail, insufficient data for a chart:**
```
Not enough history yet for this time range.
Check back after a few more monitoring cycles.
```

Each empty state explains *why* the screen is empty and, where relevant, offers the next action (adding a service) rather than leaving the user to guess.

## 12. Error States

The UI must clearly and calmly surface failures in the application's own supporting systems, distinct from failures of a *monitored* service (which are the normal, expected content of the Dashboard and Incidents screens).

- **Database disconnected**: a persistent, non-blocking banner at the top of the window ("Lost connection to the database — retrying...") rather than a crash or a silently frozen dashboard, consistent with NFR-005's reconnect-and-retry behavior.
- **Discord webhook or SMTP delivery failure**: the corresponding Alert History row shows an explicit "Failed" outcome with a short reason (e.g., "SMTP timeout"), rather than silently omitting the row — this is the UI-facing counterpart of the Architecture document's per-channel error isolation (Section 4.4).
- **Chart query error**: the chart area shows an inline message ("Couldn't load chart data — try again") with a retry action, rather than an empty or broken chart.
- **Invalid input in Add/Edit Service dialog**: inline, field-level error text appears next to the offending field the moment it becomes invalid, and Save remains disabled until resolved — this is the "error prevention" principle from Section 3 in practice.

## 13. Desktop-First Design

LastPing is explicitly a desktop-first application. Responsive, mobile-oriented layouts are not required or planned, since LastPing is a single-user desktop monitoring tool per SC-007 and NFR-014, not a phone or tablet application. Any layout flexibility described in this document (see Section 14) refers to window resizing on desktop operating systems, not adaptive mobile breakpoints.

## 14. Window Behavior

As a native desktop application, LastPing should behave the way users expect a desktop app to behave:

- **Minimum window size**: the main window enforces a sensible minimum (e.g., 960×600) below which the Dashboard's status cards and the Service Detail chart would no longer be legibly usable.
- **Resizable**: the main window is freely resizable above that minimum, with the Dashboard's service list and chart areas expanding to fill available space.
- **Remembers last window position and size**: on relaunch, the window reopens at the size and position it was last closed at, rather than resetting to a default every time.
- **Remembers sidebar width**: if the navigation rail becomes resizable in a later version, its width is persisted the same way.

## 15. Responsiveness at Scale (Qt Performance)

Distinct from the desktop-vs-mobile question in Section 13, this section addresses how the Dashboard stays usable as the number of monitored services grows toward the NFR-001 target of ~50 concurrent services.

- **5 services**: no special handling needed; the full list renders directly.
- **20 services**: the list remains fully visible with scrolling; live status updates continue to apply in place without noticeable lag.
- **50 services**: at this scale, the Dashboard should use row virtualization (rendering only the visible rows plus a small buffer) so that scrolling and live status updates stay smooth, along with a fixed header row (search/filter bar) that remains visible while the service list scrolls beneath it.

This keeps the Dashboard's perceived performance flat regardless of service count, which is what NFR-002 (status reflected within 2 seconds) actually depends on in practice — a naive full re-render on every status change would violate that target well before 50 services.

## 16. Interaction Patterns

- **Live updates**: the Dashboard subscribes to the `service_status_changed` event described in the Architecture document; status symbols update in place without a manual refresh or a jarring full-list re-render.
- **Toasts**: transient in-app toast notifications accompany desktop notifications for down/recovery events, so a user actively looking at the app sees the same information the OS notification conveys.
- **Non-blocking dialogs**: the Add/Edit Service dialog validates and saves without freezing the dashboard behind it, consistent with the async/Qt bridge described in the Architecture document.
- **Confirm-before-destroy**: deleting a service always requires confirmation and an explicit choice about retaining historical data (FR-040).

## 17. Accessibility Considerations

- Status must never be conveyed by color alone: each status also carries a distinct icon/symbol (●, ▲, ✖, ⏸) as shown in the Legend and every wireframe above, so the interface remains legible for color-blind users.
- All interactive elements are reachable via keyboard navigation (tab order follows visual layout).
- Font sizes are not hardcoded to a fixed pixel value where Qt's scalable units can be used instead, to support OS-level text scaling.

## 18. Dark Mode (FR-042)

Dark mode is planned as a palette swap rather than a structural redesign: the same layout, spacing, and status semantics apply, with a dark background, adjusted contrast for chart lines, and desaturated status colors to avoid eye strain. This is scoped for a later 1.x release per the roadmap, but the visual design system above is written so that introducing it later does not require re-architecting screens.

## 19. Component Inventory

For implementation planning, the following reusable PySide6 widgets are anticipated:

- `ServiceCard` — status symbol, name, protocol badge, quick stats (used on Dashboard).
- `StatusBadge` — reusable colored + symbol status indicator (used on Dashboard, Service Detail, Incidents).
- `TrendChart` — PyQtGraph wrapper accepting a data range and rendering a response-time line chart with a degraded-threshold band, including its own skeleton loading and error states (Sections 10, 12).
- `ServiceFormDialog` — shared add/edit form, with inline validation (Section 12).
- `DateRangeTabs` — reusable 24h/7d/30d/custom control used on both Service Detail and Incidents.
- `AlertHistoryTable` / `IncidentTable` — sortable, filterable table widgets, each with its own empty state (Section 11).
- `EmptyStateView` — reusable placeholder shown by any list-based screen with no data.

Building these as shared components early keeps the Dashboard, Service Detail, and Incidents screens visually and behaviorally consistent as the application grows.

---

*This document should evolve alongside the Version 0.3–1.0 roadmap milestones (Dashboard, Historical metrics, Charts), and any deviation between this design and the implemented UI should be reflected back here.*