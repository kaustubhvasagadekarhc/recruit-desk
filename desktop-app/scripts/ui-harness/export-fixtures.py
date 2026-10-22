# Author: kaustubh.s.v
# Exports the workbooks (copies, read-only) to fixtures.json for the screenshot harness.
import json, os, sys, openpyxl
out = {}
for key, fn in [("company","Company_Hiring_Intelligence.xlsx"),("candidate","Candidate_Research.xlsx"),("account","Account_Research.xlsx")]:
    if not os.path.exists(sys.argv[1]+"/"+fn): continue
    wb = openpyxl.load_workbook(sys.argv[1]+"/"+fn, read_only=True, data_only=True)
    sheets = {}
    for ws in wb.worksheets:
        rows = [[("" if c is None else str(c)) for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(x.strip() for x in r)]
        if not rows: sheets[ws.title] = {"headers": [], "rows": []}; continue
        sheets[ws.title] = {"headers": rows[0], "rows": rows[1:]}
    out[key] = {"order": wb.sheetnames, "sheets": sheets}
json.dump(out, open(sys.argv[2], "w"))
for k,v in out.items(): print(k, {s: len(d["rows"]) for s,d in v["sheets"].items()})
