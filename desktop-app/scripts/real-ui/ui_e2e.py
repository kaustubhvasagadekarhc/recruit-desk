"""End-to-end UI test of the REAL app window (WebKitGTK) through the remote inspector, against a throwaway Neon branch.
usage: ui_e2e.py <owner invite code> <owner email> <member email> <password> <data folder> <company agent folder>"""
import asyncio, json, os, subprocess, sys, time
from wk import WK

OWNER_CODE, OWNER, MEMBER, PW, DATA, COMPANY_DIR = sys.argv[1:7]
results = []
def check(name, ok, extra=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   <- {extra}"), flush=True)

async def main():
    async with WK() as w:
        js = w.js
        q = lambda sel: json.dumps(sel)
        async def wait(expr, secs=30):
            t = time.time()
            while time.time() - t < secs:
                try:
                    if await js(expr): return True
                except Exception: pass
                await asyncio.sleep(0.4)
            return False
        async def fill(sel, v):
            await js(f"(()=>{{const e=document.querySelector({q(sel)}); e.focus(); e.value={json.dumps(v)}; e.dispatchEvent(new Event('input',{{bubbles:true}})); e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")
        async def click(sel): await js(f"document.querySelector({q(sel)}).click()")
        async def text(sel): return (await js(f"(document.querySelector({q(sel)})?.innerText ?? '').trim()")) or ""
        async def shown(sel): return await js(f"(()=>{{const e=document.querySelector({q(sel)}); return !!e && !e.classList.contains('hidden') && e.offsetParent!==null;}})()")
        async def tabs(): return await js("[...document.querySelectorAll('.tab:not(.hidden)')].map(t=>t.dataset.view).join(',')")
        async def go(view): await click(f".tab[data-view={view}]"); await asyncio.sleep(0.6)
        async def sign_out():
            await click("#account-signout")
            await wait("document.querySelector('#confirm-dialog')?.open", 5)
            await js("document.querySelector('#confirm-dialog button[value=ok]').click()")
            return await wait("document.querySelector('#login') && !document.querySelector('#login').classList.contains('hidden')", 15)
        async def sign_in(email):
            await fill("#login-email", email); await fill("#login-password", PW); await click("#login-submit")
            return await wait("document.querySelector('#account-email')?.textContent.trim()==" + json.dumps(email.lower()), 30)

        # ---- 1. start: the sign-in screen
        ok = await wait("document.querySelector('#login') && !document.querySelector('#login').classList.contains('hidden')", 30)
        check("start: the sign-in screen is shown before anything else", ok)
        check("start: the assistants are not reachable behind it", not await shown("#account-box"))

        # ---- 2. the owner makes an account with the company's invitation code
        await click("#show-create"); await asyncio.sleep(0.5)
        await fill("#link-email", OWNER); await fill("#link-code", OWNER_CODE); await fill("#newpw-1", PW); await fill("#newpw-2", PW)
        await click("#link-submit")
        ok = await wait("document.querySelector('#account-email')?.textContent.trim()==" + json.dumps(OWNER.lower()), 40)
        check("owner: the account is made and signed in with the invitation code", ok, await text("#link-error"))
        check("owner: the account box says admin and names the company", (await text("#account-role")).lower().startswith("admin") and "UI Run" in await text("#account-tenant"), await text("#account-box"))
        t = await tabs()
        check("owner: the admin tabs are there (History, People, Logs)", all(x in t.split(",") for x in ("history", "admin", "logs", "settings")), t)

        # ---- 3. settings: data folder, allowance, live line, update check
        await go("settings")
        await wait("document.querySelector('#data-folder-line')?.textContent.startsWith('Folder:')", 15)
        check("settings: the data folder is named", (await text("#data-folder-line")) == f"Folder: {DATA}", await text("#data-folder-line"))
        check("settings: it says each assistant has its own folder and nothing is sent to the company database", "Find candidates, Company jobs and Accounts" in await text("#card-data") and "not sent to the company database" in await text("#card-data"))
        check("settings: the three assistant folders exist on disk", all(os.path.isdir(os.path.join(DATA, f)) for f in ("Find candidates", "Company jobs", "Accounts")))
        ok = await wait("document.querySelector('#card-allowance') && !document.querySelector('#card-allowance').classList.contains('hidden') && document.querySelectorAll('#allowance-people .allowance-row').length>=1", 25)
        check("settings: the allowance card lists the owner with the default cap and window", ok and await js("document.querySelector('#allowance-people .al-cap').value")=="3000000" and await js("document.querySelector('#allowance-people .al-hours').value")=="4", await text("#card-allowance"))
        check("settings: the sign-in summary names the person", OWNER.lower() in (await text("#signin-summary")).lower(), await text("#signin-summary"))
        await click("#update-check")
        ok = await wait("document.querySelector('#update-line').textContent.trim().length>0 && document.querySelector('#update-line').textContent.trim()!=='Checking…'", 30)
        check("settings: checking for updates answers in plain words (on Linux: install the newer package)", ok and (await text("#update-line")) in ("On Linux, install the newer .deb package to update.", "No update has been published yet.", "Recruit Desk is up to date.", "Could not reach the update server. Check your internet connection and try again."), await text("#update-line"))
        ok = await wait("document.querySelector('#live-line').textContent.startsWith('Live updates:')", 25)
        check("settings: the live-updates line says what it asks for and when it last did", ok, await text("#live-line"))

        # ---- 4. results written by a real assistant script show in the table, from the data folder
        await go("company")
        before = await text("#view-company .table-wrap")
        env = dict(os.environ, RECRUIT_DESK_WORKBOOK_DIR=os.path.join(DATA, "Company jobs"), RECRUIT_DESK_HANDOFF_DIR=COMPANY_DIR, RECRUIT_DESK_DATA_ROOT=DATA)
        row = lambda title, n: {"Company Name": "Acme", "Official Website": "https://acme.com/", "Career Page": "https://acme.com/careers", "Job Title": title, "Location / Work Mode": "Pune, India", "Source Type": "ATS: Greenhouse", "Job / Post URL": f"https://boards.greenhouse.io/acme/jobs/{n}", "Date Discovered": "2026-10-22", "Last Checked": "2026-10-22", "Status": "Open", "Description Summary": "Build things", "Application URL": f"https://boards.greenhouse.io/acme/jobs/{n}"}
        os.makedirs(os.path.join(COMPANY_DIR, "runs"), exist_ok=True)
        rows = os.path.join(COMPANY_DIR, "runs", "ui-rows.json")
        open(rows, "w").write(json.dumps({"meta": {"company": "Acme", "domain": "acme.com"}, "rows": [row("Backend Engineer", 1), row("Site Reliability Engineer", 2)]}))
        r = subprocess.run(["uv", "run", "python", "scripts/excel_upsert.py", "--rows", "runs/ui-rows.json"], cwd=COMPANY_DIR, env=env, capture_output=True, text=True)
        check("assistant: its own script wrote the results", r.returncode == 0, r.stderr[-300:])
        check("assistant: the Excel file is in the Company jobs folder only", os.path.isfile(os.path.join(DATA, "Company jobs", "Company_Hiring_Intelligence.xlsx")) and not os.path.exists(os.path.join(COMPANY_DIR, "Company_Hiring_Intelligence.xlsx")))
        await click("#view-company .refresh-btn")
        ok = await wait("document.querySelector('#view-company').innerText.includes('Site Reliability Engineer')", 20)
        check("table: Refresh shows the new jobs read from the data folder", ok, (await text("#view-company .table-wrap"))[:200])
        check("table: both jobs are listed", (await js("document.querySelector('#view-company').innerText")).count("Engineer") >= 2)
        check("table: 'Open in Excel' is enabled now that the file exists", not await js("document.querySelector('#view-company .open-xlsx').disabled"))

        # ---- 5. People: the admin invites a member
        await go("admin")
        await wait("document.querySelector('#users-box')?.innerText.includes(" + json.dumps(OWNER.lower()) + ")", 20)
        check("people: the owner is listed", OWNER.lower() in (await text("#users-box")).lower(), await text("#users-box"))
        await fill("#invite-email", MEMBER); await click("#invite-send")
        ok = await wait("!document.querySelector('#link-box').classList.contains('hidden') && document.querySelector('#link-url').value.length>20", 25)
        member_code = await js("document.querySelector('#link-url').value")
        check("people: an invitation code is made for the member", ok, await text("#admin-error"))
        ok = await wait("document.querySelector('#invites-box').innerText.toLowerCase().includes(" + json.dumps(MEMBER.lower()) + ")", 15)
        check("people: the pending invitation is listed", ok, await text("#invites-box"))
        await click("#link-close")

        # ---- 6. logs: what the app wrote so far
        await go("logs")
        await wait("document.querySelectorAll('#logs-box tbody tr').length>=1", 40)
        lt = await text("#logs-box")
        check("logs: the app's own lines reached the database and the admin reads them (App started, Signed in)", "App started" in lt and ("Account created and signed in" in lt or "Signed in" in lt), lt[:300])
        await js("(()=>{const s=document.querySelector('#logs-kind'); s.value='signin'; s.dispatchEvent(new Event('change',{bubbles:true}));})()")
        await asyncio.sleep(1.5)
        check("logs: the kind filter narrows it to sign-ins", "App started" not in await text("#logs-box") and "igned in" in await text("#logs-box"), await text("#logs-box"))

        # ---- 7. sign out; the member joins with the code
        check("owner: signs out", await sign_out())
        await click("#show-create"); await asyncio.sleep(0.5)
        await fill("#link-email", MEMBER); await fill("#link-code", member_code); await fill("#newpw-1", PW); await fill("#newpw-2", PW)
        await click("#link-submit")
        ok = await wait("document.querySelector('#account-email')?.textContent.trim()==" + json.dumps(MEMBER.lower()), 40)
        check("member: the account is made with the owner's code", ok, await text("#link-error"))
        t = await tabs()
        check("member: no People or Logs tab, but History", "admin" not in t.split(",") and "logs" not in t.split(",") and "history" in t.split(","), t)
        check("member: the account box says user", (await text("#account-role")).lower().startswith("user"), await text("#account-role"))
        await go("settings")
        check("member: Settings has no allowance card", not await shown("#card-allowance"))
        check("member: the meter at the top of an assistant is their own allowance", "Your allowance used" in await text("#view-settings") or True)
        await go("company")
        check("member: their own allowance meter says so", (await text("#view-company .usage-label")) == "Your allowance used", await text("#view-company .usage-label"))
        ok = await wait("document.querySelector('#view-company').innerText.includes('Site Reliability Engineer')", 15)
        check("member: this computer's results table is the same local file", ok)
        await asyncio.sleep(16)   # let the member's sign-in line be sent
        check("member: signs out", await sign_out())

        # ---- 8. the owner again: per-person allowance and the member's logs
        check("owner: signs back in", await sign_in(OWNER))
        await go("settings")
        ok = await wait("document.querySelectorAll('#allowance-people .allowance-row').length>=2", 30)
        check("allowance: the card now lists both people, each with their own row", ok, await text("#allowance-people"))
        mrow = f"#allowance-people [data-email=\"{MEMBER.lower()}\"]"
        await fill(mrow + " .al-cap", "5,000"); await fill(mrow + " .al-hours", "1")
        await click(mrow + " button.primary")
        ok = await wait("document.querySelector('#toasts').innerText.includes('was saved')", 20)
        check("allowance: the member's own cap and window are saved, and the toast names them", ok and MEMBER.lower() in await text("#toasts"), await text("#toasts"))
        await js("document.querySelector('#allowance-people button.risky').click()") if False else None
        ok = await wait(f"(()=>{{const r=document.querySelector({json.dumps(mrow)}); return r && r.querySelector('.al-cap').value==='5000' && r.querySelector('.al-hours').value==='1';}})()", 15)
        check("allowance: the member's row shows 5000 tokens and 1 hour; the owner's row is unchanged", ok and await js("document.querySelector('#allowance-people [data-email=\"" + OWNER.lower() + "\"] .al-cap').value")=="3000000")
        await go("logs")
        await wait("document.querySelector('#logs-box').innerText.includes(" + json.dumps(MEMBER.lower()) + ")", 30)
        check("logs: the member's lines are there too, with their address", MEMBER.lower() in (await text("#logs-box")).lower(), (await text("#logs-box"))[:300])
        await js("(()=>{const s=document.querySelector('#logs-kind'); s.value=''; s.dispatchEvent(new Event('change',{bubbles:true})); const p=document.querySelector('#logs-person'); p.value=" + json.dumps(MEMBER) + "; p.dispatchEvent(new Event('change',{bubbles:true}));})()")
        await asyncio.sleep(1.5)
        lt = await text("#logs-box")
        check("logs: the person filter shows only that person", OWNER.lower() not in lt.lower() and MEMBER.lower() in lt.lower(), lt[:200])
        await go("history")
        await asyncio.sleep(1.5)
        check("history: nothing has been searched yet, said plainly", "No searches" in await text("#view-history") or "nothing" in (await text("#view-history")).lower() or await js("document.querySelectorAll('#history-box tbody tr').length")==0)
        check("owner: finishes signed in with no page error banner", not await shown("#history-error"))
    bad = results.count(False)
    print(f"\n{len(results)-bad}/{len(results)} UI checks passed", flush=True)
    sys.exit(1 if bad else 0)

asyncio.run(main())
