---
name: contacts
description: Step 4b of /account-scan. Leaders and contacts - up to 6 senior people with title, location, source and published channels, plus the company's own published channels (press, sales, partnerships, investor relations, contact form, offices). Never a guessed email or phone.
---

# Leaders and contacts

Runs after `leadership` and `india-connection`, inside step 4, on the people and pages already found.
`progress.py sub "contacts: ..."` after each part.

## Apollo first, when configured (the one approved paid source)

`uv run python scripts/lookup.py apollo <domain> --json` (`progress.py sub "contacts: Apollo ..."` with its result).
- `Apollo not configured`: carry on with the free steps below; nothing else changes.
- Otherwise it prints up to 6 senior people as ready `_contacts` entries: `"source": "apollo"`, `"apollo_id"`, an
  email only when Apollo marked it `email_status: "verified"` (`{"value", "source": "apollo", "apollo_id",
  "email_status": "verified"}`), a phone only when Apollo returned it directly, and the last line `Apollo credits
  used: N (...)`. Copy those entries as printed; never edit an Apollo value, never add one Apollo did not return, never
  turn a dropped (guessed, likely, unavailable) email into a value. `Apollo stopped: ...` (key rejected, plan or
  credits, rate limit, budget) means: keep what was printed and carry on free-only.
- **Cross-check every Apollo person against public sources** in the free steps below: confirm the name and title on
  the company's own pages, a press release or a search result, and where you find that public page, use its URL as
  the person's `source` (keep `apollo_id`). An Apollo person whose role a public source contradicts (left the
  company, other title) is dropped. Fill the channels (LinkedIn, X, GitHub, ...) from public sources as below.
- Report `Apollo credits used: N` in the step-4 done line and the step-8 contacts line.

## People (`_contacts`, at most 6)

Pick from the leaders already found, most senior first: CEO, founders, CTO, VP or Head of Engineering, COO, CHRO
or talent head, and any US-based leader. For each:

- `name`, `title`, `location` (city, country as stated, or `Not published (checked: ...)`), `source` (the URL that
  names them in that title), `verified` (today's date, the day you read that source).
- `email`: **only** if the person or the company published it. Run `uv run python scripts/lookup.py contact <url>`
  on the person's own site, bio or speaker page; it prints each email with the words around it. Write
  `{"value": "jane@acme.com", "quote": "<those words, containing the address>", "source": "<that URL>"}`.
  Otherwise `Not published (checked: <pages and searches tried>)`.
- `phone`: the same rule; `"kind": "personal"` when the person published it. With no personal number, use the
  company's published main number as `{"value": ..., "kind": "company line", "quote": ..., "source": <contact page>}`.
- `linkedin`: a `https://www.linkedin.com/in/<id>` URL **as it appears in an Exa search result**. Never fetch it.
- `x`, `github`, `site` (personal site or blog), `podcast` (their podcast or newsletter), `talks` (an upcoming talk:
  `"<event, date> https://..."`), `booking` (a booking link they published themselves): a URL each, from their own
  pages, the company site or search results.
- `channels_checked`: what you searched for this person's channels (used for every channel left out).

**Never** build an address from a pattern (firstname@, first.last@, "likely", "probably"), never guess a phone,
never use Apollo outside `lookup.py apollo` (no Apollo web pages, no other Apollo route), never use Hunter, RocketReach, ZoomInfo, Lusha, ContactOut or any people-search or data-broker site, never
log in or read anything behind a login. The hook refuses an email or phone whose quote does not contain it, and
any guess wording.

Exa searches (count against the 25 per company; usually 3 to 5 here): `"<Company> <title> twitter"`,
`"<Company> CTO github"`, `"<Company> <title> podcast"`, `"<Company> <title> speaker <this year>"`. Search by company
and role; a name only to confirm a page already found.

## Company channels (`_channels`)

`uv run python scripts/lookup.py contact <domain>` reads the contact, press, newsroom, media, investors, partners,
sales and offices pages and prints every published email, phone, contact form and address-like line with its
quote and URL. Fill `press_email`, `sales`, `partnerships`, `investor_relations`, `contact_form` as
`{"value", "source", "quote"}` (the quote is required when the value is an email or phone) and `offices` as
`[{"label": "HQ", "address": "...", "phone": "...", "source": "...", "quote": "<words containing address and phone>"}]`
(at most 6, HQ, US and India first). `checked`: the pages and searches tried; every channel left out is written
as `Not published (checked: <checked>)`.

## Check before the draft

After the draft is written: `uv run python scripts/contacts.py verify --draft runs/<slug>.draft.json`. It re-reads
each email and phone source page and confirms the value is on it (Apollo values are checked for their person id
and verified status instead; they are not on a page). Replace any that fail with
`Not published (checked: ...)` and Write the draft again. Then `progress.py sub "contacts: <p> people, <e>
published emails, <ph> published phones, <c> other channels"`.
