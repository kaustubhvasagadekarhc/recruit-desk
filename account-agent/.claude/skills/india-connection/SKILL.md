---
name: india-connection
description: Step 4 of /account-scan. Fill "India Connection (US-based leader)" only from self-published, cited evidence after searching every US-based leader's own bios and talks; never from a name or photo.
---

# India Connection (US-based leader)

From the leaders found in step 4, consider those **based in the US** (a US location on the team page, a talk
bio, a filing address or a public profile snippet). For an India-headquartered company, also look for its US
leaders (head of US, US GM, US country manager, US-based board members) with Exa `"<Company> US head"`,
`"<Company> USA general manager"`.

## Where to look (per US-based leader, until a signal is found)

1. Their bio on the company's own site (team / leadership / about / press release announcing the hire).
2. Conference and event speaker pages: Exa `"<Company> <title> speaker"`, `"<Company> keynote"`,
   `"<Company> SaaSBoomi OR NASSCOM OR TiE OR India"`. Speaker bios are usually written by the speaker.
3. Podcasts and interviews: Exa `"<Company> <title> podcast"`, `"<Company> <title> interview"`.
4. Their own published profiles found as Exa snippets (personal site, GitHub, Medium, X, LinkedIn snippet text
   only, never fetched): education and past work as they state it.
5. Alumni pages of Indian institutions that name them (`"<Company> <title> IIT alumni"`).


Company-level context (not a signal for this cell): `lookup.py h1b "<Company>"` shows the company's US H-1B LCA
filings, top roles and worksites. Use it to decide where to look (a US engineering team that hires on H-1B often has
leaders who built India teams), and mention it in your report, but never write it here and never as a fact about a
person: LCA data has no nationality. `assemble.py` adds it to Development Requirement and `excel_upsert.py` to the
H-1B columns.
## Signals and cell text

| Signal | Example of the cell text |
| --- | --- |
| Studied at an Indian institution | `Yes: CTO Jane Doe (San Francisco) studied at IIT Delhi [3]` |
| Worked in India | `Yes: VP Eng Sam Roe (Austin) previously led engineering at Flipkart, Bengaluru [5]` |
| Leads or built an India team or office | `Yes: COO (New York) set up the Pune development centre [6]` |
| Public bio or interview mentions India | `Yes: CEO (Seattle) says in a podcast he grew up in Chennai [7]` |
| Speaks at India-focused events | `Yes: CTO (Boston) spoke at SaaSBoomi 2026 [8]` |
| India-focused community role, self-stated | `Yes: CEO is a charter member of TiE Silicon Valley [9]` |

Rules:
- **Only self-published evidence counts**, with a short quote or evidence phrase and its exact URL in Sources.
- **Never infer from a name, surname, photo, language or religion.** Never write "Indian"; state the fact.
- Nothing found after the search: `No evidence found (checked: <leaders checked>, <sources tried>, <n> Exa searches)`,
  e.g. `No evidence found (checked: 3 US-based leaders' bios on razorpay.com/us, 2 speaker pages, 4 Exa searches)`.
  No US-based leader at all: `No evidence found (checked: no US-based leader found on <pages>, <n> Exa searches)`.
  A bare `No evidence found` is refused by the hook.
- The hook rejects a "Yes" without a cited URL and rejects inference wording; do not try to work around it.
