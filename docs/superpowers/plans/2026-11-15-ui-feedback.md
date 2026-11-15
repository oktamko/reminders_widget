# UI feedback from daily use — 2026-11-15

Martin's notes after using the widget (original wording in Slovak, kept for reference).

| # | Note (Martin) | What it means | Status |
|---|---|---|---|
| 1 | „ked davam new reminder tak mi to da do zlozky ktoru mam ako prvu otvorenu no ja si chcem nejak interaktivne vybrat a defaultne aby to davalo do tej prvej otvorenej" | When adding a reminder, let me pick its list interactively; default stays the first open (selected) list | done |
| 2 | „hore ked si vyberam listy tak tam neni moznost zez vsetky naraz" | The list picker needs a one-click "all lists" option | done |
| 3 | „chcem odstranit moznost desktop widget/normal window bude to iba widget" | Remove the desktop widget / normal window switch (picker and tray); the app is only the desktop widget | done |
| 4 | „new sa ukaze iba pri prejdeni na plusko ktore bude vzdy posledne v liste" | The "New reminder" field appears only when hovering the "+", which is always the last item of the list | done |
| 5 | „zrusit posuvnik vpravo" | No visible scrollbar on the right (the list still scrolls) | done |
| 6 | „lepsie zalozky (podla casu, denne atd)" | Better views by time (e.g. today, by day) — details to agree | done |

## How each was done (branch `ui-feedback`)

1. A list tag next to the "New reminder" field (default: the first open list); clicking it opens a menu of all lists; the pick is kept while the list exists. A reminder added to a list that is not shown gives a short "Added to …" note. `Api.add(title, list_id=None)`.
2. "All lists" at the top of the list picker.
3. The desktop widget / normal window switch is gone from the picker and the tray; a saved "window" setting is ignored (`WindowHost.mode` is always "desktop"); `Api.set_mode` removed.
4. The "+" is always the last row of the list; the field and the list tag slide out on hover (and stay while focused or typed into).
5. The list scrolls without a visible scrollbar.
6. Both, as agreed: a third sort "By date" (sort button cycles priority → date → iCloud order) with day headings Overdue · Today · Tomorrow · Next 7 days · Later · No date; and smart views Today (due today or earlier) and Scheduled (everything with a date), across all lists, in the picker. A reminder added in Today is due today. `Api.set_view`, `view.day_group`.
