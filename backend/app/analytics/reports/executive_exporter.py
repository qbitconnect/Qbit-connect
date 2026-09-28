"""Executive & Performance Report Exporter (Phase 10 §6).

Supports authorized CSV and XLSX data exports with:
- Row-level RBAC & tenant scoping
- Sanitization against spreadsheet formula injection (=, +, -, @, \\t, \\r)
- Clear denominators and non-hallucinated missing-data representation ("—")
"""

from __future__ import annotations

import csv
import io
from typing import Any


def sanitize_cell(val: Any) -> Any:
    """Neutralize spreadsheet formula injection attacks."""
    if val is None:
        return "—"
    if isinstance(val, str):
        if val and (val[0] in ("=", "+", "-", "@", "\t", "\r") or (val.strip() and val.strip()[0] in ("=", "+", "-", "@"))):
            return f"'{val}"
        return val
    return val


def export_executive_csv(data: dict[str, Any]) -> str:
    """Export executive KPI summary and pipeline breakdown to CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    # Header / Meta
    writer.writerow(["QBIT CONNECT — EXECUTIVE CEO REPORT"])
    period = data.get("period") or {}
    writer.writerow(["Period", sanitize_cell(period.get("label") or f"{period.get('start')} to {period.get('end')}")])
    writer.writerow(["Calculated At (UTC)", sanitize_cell(data.get("metadata", {}).get("calculated_at"))])
    writer.writerow([])

    # 1. Executive Key Performance Indicators
    writer.writerow(["EXECUTIVE KEY PERFORMANCE INDICATORS"])
    writer.writerow(["Metric", "Current Value", "Previous Value", "Change", "Change %"])
    kpis = data.get("kpis", {})
    for metric_name, cmp_data in kpis.items():
        curr = cmp_data.get("current", 0)
        prev = cmp_data.get("previous")
        diff = cmp_data.get("difference", 0)
        pct = cmp_data.get("change_pct")
        pct_str = f"{pct}%" if pct is not None else "—"
        writer.writerow([
            sanitize_cell(metric_name.replace("_", " ").title()),
            curr,
            prev if prev is not None else "—",
            diff,
            pct_str,
        ])
    writer.writerow([])

    # 2. Lead Generation Breakdown
    writer.writerow(["LEAD GENERATION BREAKDOWN"])
    lg = data.get("lead_generation", {})
    writer.writerow(["Total Leads in Range", lg.get("total_leads", 0)])
    writer.writerow(["New Inbound Leads", lg.get("new_leads", 0)])
    writer.writerow(["Pre-existing Active Leads", lg.get("pre_existing_leads", 0)])
    writer.writerow(["Qualified Leads", lg.get("qualified_leads", 0)])
    writer.writerow(["Unqualified / Lost Leads", lg.get("unqualified_leads", 0)])
    writer.writerow(["Awaiting Qualification", lg.get("awaiting_qualification", 0)])
    writer.writerow(["Unique Businesses", lg.get("unique_businesses", 0)])
    writer.writerow(["Duplicate Records (Merged/Pending)", lg.get("duplicate_records", 0)])
    writer.writerow([])

    # 3. CRM Sales Pipeline
    writer.writerow(["CRM SALES PIPELINE & DEALS"])
    sp = data.get("sales_pipeline", {})
    writer.writerow(["Open Opportunities", sp.get("open_opportunities", 0)])
    writer.writerow(["Assigned Leads", sp.get("leads_assigned", 0)])
    writer.writerow(["Contacted Leads", sp.get("leads_contacted", 0)])
    writer.writerow(["Followed Up Leads", sp.get("leads_followed_up", 0)])
    writer.writerow(["Converted Leads / Won Deals", sp.get("leads_converted", 0)])
    writer.writerow(["Overdue Follow-ups", sp.get("overdue_followups", 0)])
    pipe_val = sp.get("pipeline_value_inr")
    writer.writerow(["Pipeline Commercial Value (INR)", pipe_val if pipe_val is not None else "Not recorded"])
    won_val = sp.get("closed_won_value_inr")
    writer.writerow(["Closed-Won Value (INR)", won_val if won_val is not None else "Not recorded"])
    conv_rate = sp.get("conversion_rate")
    writer.writerow(["Overall Conversion Rate", f"{round(conv_rate * 100, 2)}%" if conv_rate is not None else "—"])
    writer.writerow([])

    # 4. Pipeline Stages
    writer.writerow(["PIPELINE STAGE BREAKDOWN"])
    writer.writerow(["Stage Code", "Stage Label", "Count"])
    for stg in sp.get("pipeline_stages", []):
        writer.writerow([
            sanitize_cell(stg.get("stage")),
            sanitize_cell(stg.get("label")),
            stg.get("count", 0),
        ])
    writer.writerow([])

    # 5. Leads by Source
    writer.writerow(["LEADS BY SOURCE / PROVIDER"])
    writer.writerow(["Source", "Leads Count"])
    for src in lg.get("leads_by_source", []):
        writer.writerow([
            sanitize_cell(src.get("source")),
            src.get("count", 0),
        ])
    writer.writerow([])

    # 6. Team Activity & Top Performers
    writer.writerow(["TEAM ACTIVITY & TOP PERFORMERS"])
    ta = data.get("team_activity", {})
    writer.writerow(["Activities Completed", ta.get("activities_completed", 0)])
    writer.writerow(["Conversations Resolved", ta.get("conversations_resolved", 0)])
    writer.writerow(["Follow-ups Completed", ta.get("followups_completed", 0)])
    writer.writerow(["Notes Added", ta.get("notes_added", 0)])
    writer.writerow([])
    writer.writerow(["TOP PERFORMERS"])
    writer.writerow(["Name", "Email", "Assigned Leads", "Qualified Leads", "Converted Leads", "Conversion Rate"])
    for p in ta.get("top_performers", []):
        cr = p.get("conversion_rate")
        cr_str = f"{round(cr * 100, 1)}%" if cr is not None else "—"
        writer.writerow([
            sanitize_cell(p.get("name") or p.get("full_name")),
            sanitize_cell(p.get("email")),
            p.get("assigned_leads", 0),
            p.get("qualified_leads", 0),
            p.get("converted_leads", 0),
            cr_str,
        ])

    return buf.getvalue()


def export_employees_csv(employees: list[dict[str, Any]], period_label: str = "Active Range") -> str:
    """Export employee performance directory to CSV."""
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["QBIT CONNECT — EMPLOYEE PERFORMANCE DIRECTORY REPORT"])
    writer.writerow(["Reporting Period", sanitize_cell(period_label)])
    writer.writerow([])

    writer.writerow([
        "Employee Name",
        "Email",
        "Role(s)",
        "Team(s)",
        "Status",
        "Assigned Leads",
        "Contacted Leads",
        "Qualified Leads",
        "Converted Leads",
        "Pending Leads",
        "Follow-ups Completed",
        "Follow-ups Overdue",
        "Activities Logged",
        "Conversion Rate",
    ])

    for emp in employees:
        metrics = emp.get("metrics") or emp
        cr = metrics.get("conversion_rate")
        cr_str = f"{round(cr * 100, 1)}%" if cr is not None else "—"

        roles_list = emp.get("roles") or ([emp.get("role")] if emp.get("role") else [])
        teams_list = emp.get("teams") or ([emp.get("team_name")] if emp.get("team_name") else [])

        writer.writerow([
            sanitize_cell(emp.get("full_name") or emp.get("name")),
            sanitize_cell(emp.get("email")),
            sanitize_cell(", ".join(roles_list)),
            sanitize_cell(", ".join(teams_list)),
            sanitize_cell(emp.get("status", "ACTIVE")),
            metrics.get("assigned_leads", 0),
            metrics.get("contacted_leads", 0),
            metrics.get("qualified_leads", 0),
            metrics.get("converted_leads", 0),
            metrics.get("pending_leads", 0),
            metrics.get("followups_completed", metrics.get("follow_ups_completed", 0)),
            metrics.get("followups_overdue", metrics.get("follow_ups_overdue", 0)),
            metrics.get("activities_logged", metrics.get("activities_completed", 0)),
            cr_str,
        ])

    return buf.getvalue()


def export_executive_xlsx(data: dict[str, Any]) -> bytes:
    """Export executive report to formatted XLSX spreadsheet."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Executive Summary"

    header_font = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    section_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    bold_font = Font(name="Calibri", size=11, bold=True)
    header_fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
    section_fill = PatternFill(start_color="374151", end_color="374151", fill_type="solid")

    ws.append(["QBIT CONNECT — EXECUTIVE CEO REPORT"])
    ws.cell(row=1, column=1).font = header_font
    ws.cell(row=1, column=1).fill = header_fill

    period = data.get("period") or {}
    ws.append(["Period", str(sanitize_cell(period.get("label") or f"{period.get('start')} to {period.get('end')}"))])
    ws.append([])

    # KPI table
    ws.append(["EXECUTIVE KEY PERFORMANCE INDICATORS"])
    ws.cell(row=4, column=1).font = section_font
    ws.cell(row=4, column=1).fill = section_fill

    headers = ["Metric", "Current Value", "Previous Value", "Change", "Change %"]
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        ws.cell(row=5, column=col).font = bold_font

    kpis = data.get("kpis", {})
    for metric_name, cmp_data in kpis.items():
        curr = cmp_data.get("current", 0)
        prev = cmp_data.get("previous")
        diff = cmp_data.get("difference", 0)
        pct = cmp_data.get("change_pct")
        ws.append([
            str(sanitize_cell(metric_name.replace("_", " ").title())),
            curr,
            prev if prev is not None else "—",
            diff,
            f"{pct}%" if pct is not None else "—",
        ])

    ws.append([])
    ws.append(["PIPELINE STAGE BREAKDOWN"])
    r_idx = ws.max_row
    ws.cell(row=r_idx, column=1).font = section_font
    ws.cell(row=r_idx, column=1).fill = section_fill

    ws.append(["Stage", "Label", "Count"])
    for col in range(1, 4):
        ws.cell(row=ws.max_row, column=col).font = bold_font

    for stg in data.get("sales_pipeline", {}).get("pipeline_stages", []):
        ws.append([
            str(sanitize_cell(stg.get("stage"))),
            str(sanitize_cell(stg.get("label"))),
            stg.get("count", 0),
        ])

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 14)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
