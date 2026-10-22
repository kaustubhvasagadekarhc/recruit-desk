"""Idempotent upsert of account rows into Account_Research.xlsx: one sheet per list, one row per company.

A row is matched by its Domain. A match is updated in place; anything else is appended, so rerunning
never adds duplicates. Columns starting with "_" are never written. Each written row is also stored in
memory.db (company, cited facts, leaders' names and titles).

Leaders and contacts (the row's "_contacts" and "_channels") go to the "Contacts" and "Company channels"
sheets, one row per person or channel, linked by Domain; a rescanned company's rows are replaced. A person's own
email and phone are kept only with --contacts (data minimisation, as in candidate-agent); a company line and
the company channels are always kept.

Evidence (scripts/evidence.py): every upsert also rewrites the written companies' rows of the "Evidence" sheet (one
row per cited fact and source) and fills the derived columns appended after the 19 (Evidence Count, Freshness, Key
Field Confidence). Existing headers and sheets are never renamed or reordered.

CLI: uv run python scripts/excel_upsert.py --rows runs/<slug>.rows.json [--list "Q4 US SaaS"] [--contacts]
     uv run python scripts/excel_upsert.py --migrate-evidence   # (alias --migrate-typed) back up workbook + memory.db,
                                                                # then build Evidence, the derived, typed and H-1B
                                                                # columns for every company in memory.db

Typed fields (scripts/typed.py) and H-1B columns (scripts/h1b.py) are appended after the derived columns. Typed values
come only from cited clauses and each gets its own Evidence row; H-1B values come from the local LCA index (left as
they are when no index exists) and cite the quarter file.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contacts  # noqa: E402
import entities  # noqa: E402
import evidence  # noqa: E402
import h1b  # noqa: E402
import last_record  # noqa: E402
import memory  # noqa: E402
import progress  # noqa: E402
import typed  # noqa: E402
from common import (  # noqa: E402
    COLUMNS, DB_PATH, DEFAULT_LIST, F_CHECKED, F_COMPANY, F_CONF, F_DOMAIN, ROOT, WORKBOOK_PATH, _FIELDS, bare_domain,
    safe_sheet_name, today,
)
from validate_rows import load_rows, validate_payload  # noqa: E402

NARROW = {"Company": 22, "Domain": 22, "Product Type": 18, "Product?": 10, "Confidence": 12, "Last Checked": 13}
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
CONF_FILL = {"High": PatternFill("solid", fgColor="E2EFDA"), "Medium": PatternFill("solid", fgColor="FFF2CC"),
             "Low": PatternFill("solid", fgColor="FCE4D6")}


H1B_COLUMNS: list[str] = _FIELDS["h1b_columns"]
ALL_COLUMNS = COLUMNS + evidence.DERIVED_COLUMNS + typed.TYPED_COLUMNS + H1B_COLUMNS
NARROW.update({c: 14 for c in evidence.DERIVED_COLUMNS + typed.TYPED_COLUMNS + H1B_COLUMNS[:1]})
EV_WIDTH = {"Domain": 22, "Field": 26, "Fact": 60, "Source URL": 50, "Source Type": 15, "Checked On": 12,
            "Age (days)": 10, "Confidence": 12}


def prepare_sheet(ws) -> None:
    for i, col in enumerate(ALL_COLUMNS, 1):
        c = ws.cell(row=1, column=i, value=col)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = NARROW.get(col, 44)
    ws.freeze_panes = "C2"
    ws.row_dimensions[1].height = 42


def is_account_sheet(ws) -> bool:
    return [ws.cell(row=1, column=i).value for i in range(1, len(COLUMNS) + 1)] == COLUMNS


def write_derived(ws, r: int, ev_rows: list[list]) -> None:
    for j, (col, v) in enumerate(evidence.derived(ev_rows).items(), len(COLUMNS) + 1):
        cell = ws.cell(row=r, column=j, value=v if v != "" else None)
        cell.alignment = Alignment(vertical="top")
        if col == evidence.F_KEY_CONF:
            cell.fill = CONF_FILL.get(str(v), PatternFill())


def write_columns(ws, r: int, values: dict, cols: list[str]) -> None:
    """Write the given appended columns of one row; a column missing from values is cleared."""
    for col in cols:
        v = values.get(col)
        cell = ws.cell(row=r, column=ALL_COLUMNS.index(col) + 1, value=None if v in (None, "") else v)
        cell.alignment = Alignment(vertical="top", wrap_text=col == H1B_COLUMNS[1])


def typed_for(row_or_facts, domain: str, from_facts: bool = False) -> tuple[dict, list[list]]:
    """Typed values and their Evidence rows; refuses (exit) a value without a stating Evidence row."""
    if from_facts:
        values, ev = typed.derive_facts(domain, row_or_facts)
    else:
        values, ev = typed.derive_row(row_or_facts)
        values, ev = typed.apply_explicit(row_or_facts, values, ev)
    errs = typed.check_typed(values, ev)
    if errs:
        progress.fail_exit(f"{domain}: typed fields refused:\n" + "\n".join(errs))
    return values, ev


def h1b_for(company: str, domain: str, index: Path | None = None) -> tuple[dict, list[list]] | None:
    """H-1B column values and Evidence rows from the local LCA index (scripts/h1b.py), or None without one."""
    got = h1b.signal(company, index)
    if got is None:
        return None
    values, rows = got
    d, t = bare_domain(domain), today()
    return values, [[d, f, fact, url, "Filing", t, 0, "High"] for f, fact, url in rows]


def write_evidence(wb, by_domain: dict[str, list[list]], replace_all: bool = False) -> int:
    """Replace the Evidence rows of the given domains (or the whole sheet). Returns the sheet's row count."""
    name = next((n for n in wb.sheetnames if n.lower() == evidence.EVIDENCE_SHEET.lower()), None)
    old: list[list] = []
    if name:
        ws = wb[name]
        old = [list(r) for r in ws.iter_rows(min_row=2, values_only=True) if any(v is not None for v in r)]
        wb.remove(ws)
    ws = wb.create_sheet(evidence.EVIDENCE_SHEET)
    for i, col in enumerate(evidence.EVIDENCE_COLUMNS, 1):
        c = ws.cell(row=1, column=i, value=col)
        c.font, c.fill = HEADER_FONT, HEADER_FILL
        ws.column_dimensions[get_column_letter(i)].width = EV_WIDTH.get(col, 20)
    ws.freeze_panes = "C2"
    keep = [] if replace_all else [r for r in old if bare_domain(str(r[0] or "")) not in by_domain]
    out = keep + [r for d in sorted(by_domain) for r in by_domain[d]]
    for ri, r in enumerate(out, 2):
        for ci, v in enumerate(r[:len(evidence.EVIDENCE_COLUMNS)], 1):
            cell = ws.cell(row=ri, column=ci, value=v)
            if ci == 4 and isinstance(v, str) and v.startswith(("http://", "https://")):
                cell.hyperlink = v
                cell.font = Font(color="0563C1", underline="single")
            if ci == 8:
                cell.fill = CONF_FILL.get(str(v), PatternFill())
    ws.auto_filter.ref = f"A1:{get_column_letter(len(evidence.EVIDENCE_COLUMNS))}{max(len(out) + 1, 2)}"
    return len(out)


def save(wb, wbp: Path) -> None:
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=wbp.parent)
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, wbp)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def backup(paths: list[Path], dest_root: Path) -> Path:
    dest = dest_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for p in paths:
        if p.exists():
            shutil.copy2(p, dest / p.name)
    return dest


def migrate_evidence(wbp: Path, db: Path | None = None, backups: Path | None = None, h1b_index: Path | None = None) -> str:
    """Back up the workbook and memory.db, then build Evidence, the derived, typed and H-1B columns from memory.db."""
    db = db or DB_PATH
    if not wbp.exists():
        return f"{wbp.name} does not exist; nothing to migrate"
    lock = wbp.with_name("~$" + wbp.name)
    if lock.exists():
        progress.fail_exit(f"{wbp.name} looks open in Excel ({lock.name} exists); close it and rerun")
    dest = backup([wbp, db], backups or ROOT / "backups")
    con = memory.connect(db)
    by_domain: dict[str, list[list]] = {}
    extra: dict[str, list[list]] = {}
    cols: dict[str, dict] = {}
    names = {bare_domain(r[0]): r[1] for r in con.execute("SELECT domain, name FROM companies")}
    for r in con.execute("SELECT DISTINCT domain FROM facts"):
        facts = [dict(x) for x in con.execute("SELECT field, value, sources, checked FROM facts WHERE domain=?", (r[0],))]
        d = bare_domain(r[0])
        by_domain[d] = evidence.evidence_from_facts(r[0], facts)
        tv, tev = typed_for(facts, d, from_facts=True)
        cols[d], extra[d] = {"typed": tv}, tev
        hv = h1b_for(names.get(d, ""), d, h1b_index) if names.get(d) else None
        if hv:
            cols[d]["h1b"] = hv[0]
            extra[d] = extra[d] + hv[1]
    wb = load_workbook(wbp)
    sheets = rows_done = 0
    for ws in wb.worksheets:
        if not is_account_sheet(ws):
            continue
        sheets += 1
        prepare_sheet(ws)
        dcol = COLUMNS.index(F_DOMAIN) + 1
        for r in range(2, ws.max_row + 1):
            d = bare_domain(str(ws.cell(row=r, column=dcol).value or ""))
            if d:
                write_derived(ws, r, by_domain.get(d, []))
                write_columns(ws, r, cols.get(d, {}).get("typed", {}), typed.TYPED_COLUMNS)
                if "h1b" in cols.get(d, {}):
                    write_columns(ws, r, cols[d]["h1b"], H1B_COLUMNS)
                rows_done += 1
        ws.auto_filter.ref = f"A1:{get_column_letter(len(ALL_COLUMNS))}{max(ws.max_row, 2)}"
    n = write_evidence(wb, {d: by_domain[d] + extra.get(d, []) for d in by_domain}, replace_all=True)
    entities.refresh(wb)
    save(wb, wbp)
    typed_n = sum(len(c["typed"]) for c in cols.values())
    h1b_n = sum("h1b" in c for c in cols.values())
    return (f"migrated: backup in {dest}; "
            f"{sheets} account sheet(s), {rows_done} rows given {', '.join(evidence.DERIVED_COLUMNS)}, "
            f"{len(typed.TYPED_COLUMNS)} typed columns ({typed_n} values filled) and {', '.join(H1B_COLUMNS)} "
            f"({h1b_n} companies; {'no LCA index' if not (h1b_index or h1b.INDEX_PATH).exists() else 'from the LCA index'}); "
            f"Evidence sheet: {n} rows for {len(by_domain)} companies")


def migrate_entities(wbp: Path, db: Path | None = None, backups: Path | None = None, dry_run: bool = False) -> str:
    """Back up the workbook and memory.db, then build the Companies master sheet from the list sheets."""
    db = db or DB_PATH
    if not wbp.exists():
        return f"{wbp.name} does not exist; nothing to migrate"
    lock = wbp.with_name("~$" + wbp.name)
    if lock.exists():
        progress.fail_exit(f"{wbp.name} looks open in Excel ({lock.name} exists); close it and rerun")
    wb = load_workbook(wbp)
    n = entities.refresh(wb)
    if dry_run:
        return f"dry run: {n} companies would be written to the Companies sheet; nothing saved, no backup"
    dest = backup([wbp, db], backups or ROOT / "backups")
    save(wb, wbp)
    return f"migrated: backup in {dest}; Companies sheet: {n} companies across {sum(entities._is_list_sheet(w) for w in wb.worksheets)} list sheet(s)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows")
    ap.add_argument("--migrate-entities", action="store_true",
                    help="back up the workbook and memory.db, then build the Companies master sheet (--dry-run: counts only)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--migrate-evidence", "--migrate-typed", dest="migrate_evidence", action="store_true",
                    help="back up the workbook and memory.db, then build Evidence and the derived, typed and H-1B "
                         "columns from memory.db")
    ap.add_argument("--list", help="sheet name (one sheet per list); default: the file's meta.list or Accounts")
    ap.add_argument("--workbook", default=str(WORKBOOK_PATH))
    ap.add_argument("--contacts", action="store_true",
                    help="keep people's own published emails and phones in the Contacts sheet (opt-in)")
    a = ap.parse_args()
    if a.migrate_evidence:
        print(migrate_evidence(Path(a.workbook)))
        return
    if a.migrate_entities:
        print(migrate_entities(Path(a.workbook), dry_run=a.dry_run))
        return
    if not a.rows:
        ap.error("--rows is required (or --migrate-evidence / --migrate-entities)")

    payload = json.loads(Path(a.rows).read_text(encoding="utf-8"))
    errs = validate_payload(payload)
    if errs:
        progress.fail_exit("refusing to write invalid rows:\n" + "\n".join(errs[:30]))
    rows = load_rows(payload)
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    sheet = safe_sheet_name(a.list or meta.get("list") or DEFAULT_LIST)
    wbp = Path(a.workbook)
    lock = wbp.with_name("~$" + wbp.name)
    if lock.exists():
        progress.fail_exit(f"{wbp.name} looks open in Excel ({lock.name} exists); close it and rerun")

    if wbp.exists():
        wb = load_workbook(wbp)
    else:
        wb = Workbook()
        wb.remove(wb.active)
    match = next((n for n in wb.sheetnames if n.lower() == sheet.lower()), None)
    ws = wb[match] if match else wb.create_sheet(sheet)
    prepare_sheet(ws)
    dcol = COLUMNS.index(F_DOMAIN) + 1
    index = {}
    for r in range(2, ws.max_row + 1):
        d = bare_domain(str(ws.cell(row=r, column=dcol).value or ""))
        if d and d not in index:
            index[d] = r
    next_row = max([1, *index.values()]) + 1
    updated = appended = 0
    newest = None   # (domain, record) of the last NEW company of this batch
    new_rows = []   # every NEW company of this batch, for the person's search history
    for row in rows:
        d = bare_domain(row[F_DOMAIN])
        r = index.get(d)
        if r is None:
            r, next_row = next_row, next_row + 1
            index[d] = r
            appended += 1
            newest = (d, {"company": row.get(F_COMPANY), "domain": d, "product_type": row.get("Product Type"), "hq": row.get("HQ / US Office"),
                          "funding": row.get("Funding"), "confidence": row.get(F_CONF), "last_checked": row.get(F_CHECKED)})
            new_rows.append(newest)
        else:
            updated += 1
        for i, col in enumerate(COLUMNS, 1):
            v = str(row.get(col) or "").strip()
            cell = ws.cell(row=r, column=i, value=v or None)
            cell.alignment = Alignment(vertical="top", wrap_text=col not in NARROW)
            if col == F_DOMAIN and v:
                cell.hyperlink = f"https://{v}"
                cell.font = Font(color="0563C1", underline="single")
            if col == F_CONF:
                cell.fill = CONF_FILL.get(v, PatternFill())
    ev = {bare_domain(row[F_DOMAIN]): evidence.evidence_for_row(row) for row in rows}
    for d, ev_rows in ev.items():
        write_derived(ws, index[d], ev_rows)
    for row in rows:
        d = bare_domain(row[F_DOMAIN])
        tv, tev = typed_for(row, d)
        write_columns(ws, index[d], tv, typed.TYPED_COLUMNS)
        ev[d] = ev[d] + tev
        hv = h1b_for(str(row.get(F_COMPANY) or ""), d)
        if hv:
            write_columns(ws, index[d], hv[0], H1B_COLUMNS)
            ev[d] += hv[1]
    ws.auto_filter.ref = f"A1:{get_column_letter(len(ALL_COLUMNS))}{max(ws.max_row, 2)}"
    ev_total = write_evidence(wb, ev)
    progress.sub(f"Evidence sheet: {sum(map(len, ev.values()))} fact(s) for {len(ev)} compan(ies); {ev_total} rows in sheet")
    people = chans = 0
    if any(r.get("_contacts") is not None or r.get("_channels") is not None for r in rows):
        people, chans = contacts.write_sheets(wb, rows, keep_personal=a.contacts)
        withheld = sum(isinstance(p.get(k), dict) and (k == "email" or p[k].get("kind") != "company line")
                       for r in rows for p in r.get("_contacts") or [] for k in ("email", "phone"))
        note = "" if a.contacts or not withheld else f"; {withheld} personal email/phone value(s) not stored (no --contacts)"
        progress.sub(f"Contacts sheet: {people} people; Company channels sheet: {chans} rows{note}")
    progress.sub(f"Companies sheet: {entities.refresh(wb)} companies across all lists")

    save(wb, wbp)
    if newest:   # a plain local file; Recruit Desk stores it as the last record while its person is signed in
        last_record.write(last_record.folder(wbp.parent) / last_record.FILE_NAME, *newest)
        last_record.append_rows(last_record.folder(wbp.parent), new_rows)
    con = memory.connect()
    for row in rows:
        memory.record_row(con, row, ws.title)
    names = ", ".join(str(r.get(F_COMPANY)) for r in rows[:5])
    print(f"sheet '{ws.title}': updated {updated}, appended {appended}, rows in sheet {len(index)} -> {wbp.name}")
    progress.result(f"sheet '{ws.title}': {appended} inserted, {updated} updated ({names}); {len(index)} companies in sheet")


if __name__ == "__main__":
    main()
