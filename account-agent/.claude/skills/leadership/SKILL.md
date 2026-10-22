---
name: leadership
description: Step 4 of /account-scan. Who to Contact and Leadership Education & Background from public pages, filings, speaker pages and Exa snippets, names and titles only, every fact cited, conflicts resolved by the newest dated source.
---

# Leadership

1. Names and titles first from the company's own leadership / team / about / investors pages (`lookup.py page`),
   press releases announcing hires, Wikipedia `key_people`, a 10-K's executive officers list, and conference
   speaker pages.
2. Then hand the `leader-scout` subagent the company name, domain, size band and the titles to look for
   (founders / CTO / CEO under about 200 staff; VP Engineering, Head of Engineering, Head of Delivery, CTO above;
   plus US-based leaders for the India Connection). It returns each leader with title, location, education, past
   employers, communities and conferences, each with a URL and date, plus its `…` lines; relay those.
3. **Who to Contact**: up to 3 people, `Name, Title [n]`, best fit first. An email or phone only if the person
   published it themselves: `jane@acme.com (self-published [n])`. Never a guessed email pattern.
4. **Conflicts.** Two sources naming different holders of one title (most often CTO): search once more for the
   newest dated source (`"<Company> CTO <this year>"`, the company's newsroom) and keep that holder. If it stays
   unclear, keep both with `(conflict: <A> per [n] <date> vs <B> per [m] <date>)`. The hook refuses an
   unresolved conflict without that note.
5. **Leadership Education & Background**: per contact, `Name: B.Tech IIT Delhi [n]; ex-Flipkart, Bengaluru [n]`.
   No background after searching: `Name: not found (checked: ...)` for that person; the whole cell
   `Not found (checked: ...)` only when no contact has any.
6. Keep the evidenced facts per leader for the draft's `_leaders` list (see `write-workbook`); only facts
   with a source.

LinkedIn: Exa search snippets of public profiles may be cited by their URL; never fetch the page.
Close with `progress.py done 4 "<n> leaders, India Connection: <Yes / No evidence found>"` after the `india-connection` skill.
