# Granny V0 PRD

> Status: Draft. Items marked `[CONFIRM]` require confirmation before implementation or launch.

## Purpose

V0 is a public demo that proves Granny's value quickly. It should show that the system can turn live grocery data into sensible, visibly different shopping lists for different households, and it should create interest from both future users and developer collaborators.

This document is the current-scope PRD for the repo. The broader product vision lives in the full Granny PRD outside this repository.

## What V0 Is

- A public demo website
- Centered on 10 fictional Bay Area household personas
- Each persona has a weekly shopping list generated from live deal data
- Read-only by default, with optional lightweight interaction
- Includes email capture and a developer-facing "agent skill" section

V0 is successful when a visitor can understand the value in under a minute and either:
- leave their email for future access, or
- click into the GitHub/skill experience

## Goals

| Goal | Measure |
|---|---|
| Prove the algorithm works | 10 personas with 10 meaningfully different lists |
| Make the value clear | Visitor understands "saves me money, tells me where to go" quickly |
| Capture early users | Email signups |
| Capture developer interest | GitHub clicks or skill downloads |
| `[CONFIRM]` Validate proposition fit | Visitors identify with at least one persona |

## Personas

Personas should vary across:
- household composition
- dietary profile
- budget level
- store preferences and constraints
- shopping radius

### Proposed personas `[CONFIRM]`

| # | Name | Household | Dietary profile | Budget | Notable constraints |
|---|---|---|---|---|---|
| 1 | The Nguyens | 2 adults, 2 school-age kids | No restrictions | Budget-conscious | Costco member; will drive for savings |
| 2 | Sofia | Single professional, 20s | Pescatarian | Mid-range | Small quantities; convenience matters |
| 3 | The Garcias | 2 adults, 3 kids, 1 grandparent | Latino staples; no restrictions | Very price-sensitive | H Mart and Safeway primary |
| 4 | James & Wei | Couple, no kids | One vegan, one omnivore | Quality-first | Whole Foods comfortable; no Costco |
| 5 | The Patels | 2 adults, 1 toddler | Vegetarian; Indian cuisine | Mid-range | Specific ingredients; freshness priority |
| 6 | Karen | Single parent, 1 teenager | Gluten-free (medical) | Budget-conscious | Limited time; efficiency above all |
| 7 | The Kims | 2 adults, 2 young kids | Korean staples; no restrictions | Mid-range | H Mart primary; multi-store comfortable |
| 8 | David | Single adult, 30s | Omnivore; meal-preps weekends | Mid-range | Buys in bulk; Costco member |
| 9 | The Johnsons | 2 adults, empty nesters | One diabetic; low-sugar | Mid-range | Whole Foods and Safeway only |
| 10 | Rosa | Elderly, lives alone | No restrictions; small portions | Very price-sensitive | 1-2 stores max |

## Product Surface

### Persona cards

- household summary
- dietary or budget notes
- `[CONFIRM]` whether assumptions are shown in full or summarized

### Shopping list per persona

- items grouped by store
- store visit order
- one-line explanation per item
- price per item and total spend
- estimated savings
- `[CONFIRM]` whether trip time is shown

### Email capture

- one-field signup
- no account required
- `[CONFIRM]` exact promise: waitlist, free trial, or early access
- `[CONFIRM]` what happens after signup

### Developer / agent skill section

- public GitHub repo link
- downloadable skill manifest preview
- `[CONFIRM]` whether download is public or email-gated
- `[CONFIRM]` final CTA copy

### Bonus `[CONFIRM]`

- visitor can build a personal list by adding/removing from persona lists
- visitor can save or email that list

## What V0 Is Not

- not conversational
- not personalized
- not a live MCP server
- not the full V1 product
- not the subscription product

## Success Criteria

| Metric | Target |
|---|---|
| Email signups | `[CONFIRM]` |
| GitHub stars or skill downloads | `[CONFIRM]` |
| Time on persona page | >60 seconds average |
| Return visits | `[CONFIRM]` |
| Qualitative | Visitors identify with a persona unprompted |

## Open Questions `[CONFIRM]`

1. Are the 10 personas approved?
2. How many stores are live at V0 launch?
3. Show trip time or only visit order?
4. Show full assumptions or summaries?
5. What exactly is the email signup promise?
6. What post-signup flow exists?
7. Is skill download gated?
8. What is the final CTA copy?
9. Are personal-list bonus features in scope?
10. What are the numeric launch targets?

## Relation To This Repo

This repository currently contains the ingestion, publishing, admin, and local app foundation that V0 depends on. The full route optimization and full product vision are broader than what is currently implemented here.

Current implementation note:

- the local scheduler currently runs one shared weekly job for all supported stores
- true per-store schedule handling is still future work
- the next major infrastructure priority is the `item_price_history` database for historical price benchmarks
