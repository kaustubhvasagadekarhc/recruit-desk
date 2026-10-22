---
name: starter
description: Step 6 of /account-scan. Write the Conversation Starter only from cited facts already in the row, then check it with starter.py.
---

# Conversation starter

1. `start 6`, then `uv run python scripts/starter.py facts --rows runs/<slug>.rows.json`. Use only what it prints.
2. Pick the freshest hook (within about 90 days if possible): funding round; product launch; senior hire or new
   office (especially India); a talk, podcast or post by the contact; a hiring spike in roles HumanCloud staffs
   (Development Requirement).
3. Write: `Email: <at most 2 sentences, with [n]> Conference: <1 sentence, with [n]> Facts used: [n], [m]`.
   Every number you use must already appear in another cell of the row. No flattery about personal traits;
   nothing about ethnicity, religion, family or health; never mention the India Connection field.
4. `uv run python scripts/starter.py set --rows runs/<slug>.rows.json --text "<starter>"`. If it reports a
   problem, you may fix the text once and run it again; otherwise leave it blank:
   `starter.py blank --rows runs/<slug>.rows.json --reason "<why>"` (no cited hook → blank).
