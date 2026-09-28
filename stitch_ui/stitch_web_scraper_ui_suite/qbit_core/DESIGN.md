---
name: QBIT Core
colors:
  surface: '#0d1322'
  surface-dim: '#0d1322'
  surface-bright: '#333949'
  surface-container-lowest: '#080e1d'
  surface-container-low: '#151b2b'
  surface-container: '#191f2f'
  surface-container-high: '#242a3a'
  surface-container-highest: '#2f3445'
  on-surface: '#dde2f7'
  on-surface-variant: '#c2c6d8'
  inverse-surface: '#dde2f7'
  inverse-on-surface: '#2a3040'
  outline: '#8c90a1'
  outline-variant: '#424656'
  surface-tint: '#b3c5ff'
  primary: '#b3c5ff'
  on-primary: '#002b75'
  primary-container: '#0066ff'
  on-primary-container: '#f8f7ff'
  inverse-primary: '#0054d6'
  secondary: '#a5e7ff'
  on-secondary: '#003543'
  secondary-container: '#00d2ff'
  on-secondary-container: '#00566a'
  tertiary: '#4edea3'
  on-tertiary: '#003824'
  tertiary-container: '#008259'
  on-tertiary-container: '#e1ffec'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#dae1ff'
  primary-fixed-dim: '#b3c5ff'
  on-primary-fixed: '#001849'
  on-primary-fixed-variant: '#003fa4'
  secondary-fixed: '#b6ebff'
  secondary-fixed-dim: '#47d6ff'
  on-secondary-fixed: '#001f28'
  on-secondary-fixed-variant: '#004e60'
  tertiary-fixed: '#6ffbbe'
  tertiary-fixed-dim: '#4edea3'
  on-tertiary-fixed: '#002113'
  on-tertiary-fixed-variant: '#005236'
  background: '#0d1322'
  on-background: '#dde2f7'
  surface-variant: '#2f3445'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 44px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 26px
    letterSpacing: -0.01em
  headline-sm:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '600'
    lineHeight: 22px
    letterSpacing: -0.005em
  body-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.03em
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
  code-xs:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 16px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 1rem
  margin: 1.5rem
  space-2xs: 0.125rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
  space-2xl: 2rem
---

## Brand & Style

This design system delivers a high-density, mission-critical workspace tailored for growth hackers, data engineers, and revenue intelligence operators. Built upon a dark enterprise foundation, the aesthetic communicates precision, computing power, and real-time responsiveness.

### Design Movement & Mood
- **Style Archetype:** Modern Corporate High-Density dark interface with tactical instrument styling.
- **Atmosphere:** Deep navy/slate void paired with electric blue guidance vectors, vibrant emerald active states, and razor-sharp data displays.
- **Tactile Feeling:** High precision, zero visual waste, low cognitive fatigue over prolonged usage cycles, and instantaneous status communication through concentrated micro-accents.

## Colors

The color palette uses layered navy surfaces to provide visual depth without ambient clutter. Vibrant functional colors indicate execution status, telemetry, and critical warnings.

### Hierarchy & Functional Meaning
- **Primary Canvas & Surfaces:**
  - `#090D16` serves as the canvas backing.
  - `#0D1322` and `#131B2E` act as surface containers and panel wells.
  - `#1E293B` and `#1F2E47` provide structural 1px dividers and borders.
- **Accents & Interactions:**
  - `#0066FF` is the interactive anchor for action buttons, selection indicators, and primary focus rings.
  - `#00D2FF` brings electric highlights to active tabs, analytical charts, and real-time data badges.
- **Telemetry & Status Roles:**
  - Emerald Green (`#10B981`, `#059669`) strictly represents running actors, live workers, and healthy data streams.
  - Amber / Orange (`#F59E0B`, `#D97706`) indicates paused jobs, approaching credit limits, or duplicate record warnings.
  - Crimson (`#EF4444`, `#DC2626`) indicates failed requests, syntax errors, and run terminations.

## Typography

The type system prioritizes high-density data legibility and optical vertical rhythm across complex tables, multi-pane drawers, and execution streams.

### Principles
- **Primary Typeface:** `Inter` handles all navigation, table cells, metric displays, and UI copy. Medium and SemiBold weights provide contrast against dark backgrounds.
- **Monospace Companion:** `JetBrains Mono` handles real-time execution terminals, log timestamps, scraped JSON payloads, and dynamic code configuration views.
- **Color Attenuation:** Headings use `#F8FAFC`, secondary body text uses `#94A3B8`, and disabled states or timestamps use `#64748B`.

## Layout & Spacing

A compact 4px spatial rhythm underpins the entire platform, balancing information density with scannability across multi-column tables, dual-pane execution inspectors, and side-nav structures.

### Structural Models
- **Application Shell:** Fixed left navigation sidebar (64px collapsed, 240px expanded) paired with a persistent 56px global header containing search, global status, and workspace controls.
- **Main Canvas:** Dynamic 12-column responsive grid with a standard `1rem` (16px) gutter and `1.5rem` (24px) canvas margin.
- **Inspector Drawers:** Fixed 420px or 560px contextual sliding drawers for deep lead review and JSON inspectors, overlaying the main workspace with a dark backdrop.
- **Responsive Adaptations:** Below 1024px, the sidebar collapses into a persistent icon rail. Below 768px, multi-column tables switch to card lists, and secondary panels move from horizontal split into full-screen stack drawers.

## Elevation & Depth

Visual hierarchy relies on deliberate surface color stacking combined with low-contrast structural borders, rather than heavy drop shadows.

### Elevation Hierarchy
- **Level 0 (Canvas Base):** `#090D16` creates the foundational layer for page backgrounds and outer rails.
- **Level 1 (Card & Section Containers):** `#0D1322` paired with a crisp 1px stroke of `#1E293B`.
- **Level 2 (Active Panels & Toolbars):** `#131B2E` with a 1px border of `#1F2E47`.
- **Level 3 (Popovers, Modals & Dropdowns):** `#182238` backed by `rgba(0, 0, 0, 0.65)` scrim, reinforced with `0 12px 32px -4px rgba(0, 0, 0, 0.75)` and a subtle `border: 1px solid rgba(0, 102, 255, 0.2)`.
- **Active Accent Glow:** High-priority or active running indicators leverage subtle ambient drop-glows (e.g., `box-shadow: 0 0 12px rgba(0, 102, 255, 0.35)` on primary actions or `0 0 8px rgba(16, 185, 129, 0.4)` on running badges).

## Shapes

To retain an engineered, operational identity, shape styling stays concise and strictly constrained.

### Radius Assignments
- **Core Elements (`rounded` / 4px - 6px):** Form inputs, buttons, table cell wrappers, and status tags use a base 4px to 6px corner radius.
- **Panels & Cards (`rounded-lg` / 8px):** Marketplace actor cards, metric tiles, charts, and drawer surfaces use an 8px radius.
- **Complex Containers (`rounded-xl` / 12px):** Top-level modal dialogues and highlighted promotional callout cards use a 12px radius.
- **Status Pills (`rounded-full` / 9999px):** Live execution indicator pills, running count tags, and avatar badges use a complete pill roundness.

## Components

### Buttons
- **Primary:** Filled `#0066FF`, white text, 6px radius, hover state `#2563EB`, active state `#1D4ED8`. Focus state applies a 2px offset ring of `rgba(0, 210, 255, 0.6)`.
- **Secondary / Outline:** Background `transparent`, border `1px solid #1E293B`, text `#F1F5F9`. Hover state switches surface to `#131B2E` and border to `#2563EB`.
- **Destructive:** Border and background tinted crimson (`rgba(239, 68, 68, 0.1)`), text `#F87171`. On hover: surface `#DC2626`, text `#FFFFFF`.
- **Size Options:**
  - Micro / Table Action: 28px height, 11px font size.
  - Standard: 36px height, 13px font size.
  - Prominent: 42px height, 14px font size.

### Inputs & Select Fields
- **Container:** Background `#0D1322`, border `1px solid #1E293B`, 6px border radius, 36px height.
- **Text & Placeholder:** Input text `#F8FAFC`, placeholder text `#475569`.
- **Focus:** 1px border `#0066FF`, box-shadow `0 0 0 2px rgba(0, 102, 255, 0.25)`.
- **JSON Editor Well:** Background `#070A10`, 1px border `#1E293B`, JetBrains Mono typography, custom cyan scrollbars.

### Status Pills & Badges
- **Running / Active:** Background `rgba(16, 185, 129, 0.12)`, border `1px solid rgba(16, 185, 129, 0.3)`, text `#34D399`, accompanied by a 6px pulsing emerald circle.
- **Paused / Queued:** Background `rgba(245, 158, 11, 0.12)`, border `1px solid rgba(245, 158, 11, 0.3)`, text `#FBBF24`.
- **Failed / Error:** Background `rgba(239, 68, 68, 0.12)`, border `1px solid rgba(239, 68, 68, 0.3)`, text `#F87171`.

### Data Grid & Tables
- **Header:** Sticky row with `#0D1322` background, bottom border `1px solid #1E293B`, text `#64748B`, 11px uppercase semi-bold.
- **Row:** Height 40px, default background `transparent`, alternate row optional `#0B0F1A`, bottom border `1px solid #151D2E`.
- **Hover:** Background `#131B2E` with an electric blue left accent border indicator.
- **Checkboxes:** 14px square, 3px radius, border `1px solid #334155`, checked fill `#0066FF`.

### Cards & Actor Tiles
- **Marketplace Card:** Background `#0D1322`, border `1px solid #1E293B`, 8px radius, 16px internal padding.
- **Interaction:** Hover triggers border transition to `rgba(0, 102, 255, 0.45)` with a faint cyan glow shadow `0 4px 20px -2px rgba(0, 102, 255, 0.15)`.

### Execution Terminal & Live Logs
- **Background:** `#070A10` deep terminal screen.
- **Dividers:** Minimal horizontal guides with `#1E293B`.
- **Log Stream:** Timestamps `#64748B` in monospaced font, actions `#38BDF8`, successful row extractions `#10B981`, warnings `#F59E0B`.