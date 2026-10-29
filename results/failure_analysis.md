# Failure analysis: Haiku 5.5 flipped episodes

Scope: the 23 episodes in `results/flipped_haiku.jsonl` that failed under a perturbation on a task Haiku solved at baseline (10/10). Every transcript was read in full and compared with baseline runs of the same task in `results/episodes.jsonl`. Thinking blocks are empty in every episode (baseline included), so each decision is visible in the tool calls and final text.

Labels: `misread_field`, `vocab_mapping`, `wrong_tool`, `wrong_argument`, `rule_misapplied`, `no_action` (stopped without completing), `decoy`, `other`. "Caused" = does the perturbation plausibly explain the failure (yes / plausible / no).

## 1. Episode table

| # | episode_id | condition | label | caused | reason |
|---|---|---|---|---|---|
| 0 | scheduling_01_vc_room_for_five \| 5 | key_naming_synonym | other | no | Missed the 13:00-14:30 common window; declared only 09:30 and 16:00 free, so "no room meets all the requirements". Times quoted correctly from renamed keys. |
| 1 | scheduling_01_vc_room_for_five \| 6 | key_naming_synonym | other | no | Found all three windows, but treated Ember's 12:00-13:30 booking as blocking a 13:30 start: "Atlas has only 30 minutes free before 13:30"; Ember 13:30 never tested. |
| 2 | inventory_03_three_skus \| 3 | field_order_reverse | misread_field | yes | GAD-550@BOS computed as 28 available (34 on_hand minus safety_stock 6) instead of 30 (minus reserved 4); moved 2 units for a SKU that was already sufficient. |
| 3 | records_03_same_name_decoy \| 2 | key_naming_synonym | no_action | no | Correct merge plan, then paused: "LEE-301 carries a `gdpr_request` label that needs a decision first." Identical pause occurs at baseline (run 8). |
| 4 | scheduling_01_vc_room_for_five \| 1 | tool_order_reverse | other | no | Missed the 13:00-14:30 window ("only 60-minute windows... are 09:30-10:30 and 16:00-17:00"). Observations identical to baseline in this condition. |
| 5 | scheduling_01_vc_room_for_five \| 7 | format_xml | other | no | Found 13:00-14:30 but dismissed it: "those overlap with the people's own commitments in some cases" (they do not); never tested Ember at 13:30. |
| 6 | scheduling_04_client_demo_policy \| 7 | key_naming_synonym | other | no | Read policy and rooms correctly (storey 0, seats 6), then wrongly claimed Ember's 13:00-14:30 gap "would overlap the blackout or fall outside the guest's window"; 13:30-14:30 satisfies every rule. |
| 7 | records_03_same_name_decoy \| 4 | tool_order_reverse | no_action | no | Same gdpr pause: "LEE-301 carries a `gdpr_request` tag, and I'd rather check that before merging it." Tools unchanged in content; baseline shows the same behaviour. |
| 8 | records_01_latest_verified \| 8 | key_naming_synonym | no_action | plausible | Correct survivor and all three field winners, then stopped: "CRM-2044 is labelled `gdpr_request`... I'd rather you confirm that first." No key misread; 4/10 in this cell vs 0/50 elsewhere. |
| 9 | records_01_latest_verified \| 4 | key_naming_synonym | no_action | plausible | Same: "The merge policy doesn't cover labels, so I'm stopping for confirmation." Cites policy `free_text_remarks` ("Exceptions need written approval"). |
| 10 | scheduling_01_vc_room_for_five \| 4 | tool_order_reverse | other | no | Listed 13:00-14:30 as a window, then asserted Ember, Atlas and Grove are "booked during every one of those windows"; Ember is free 13:30-14:30. |
| 11 | inventory_04_rebalance_two_sites \| 1 | field_order_reverse | misread_field | yes | ATL available computed as 70 (80 minus safety_stock 10) instead of 50 (minus reserved 30); admits "I used 70 instead of 50", then adds a third DAL->ATL transfer the grader rejects. |
| 12 | scheduling_01_vc_room_for_five \| 3 | description_paraphrase | other | no | Found 13:00-14:00 / 13:30-14:30, but claimed each candidate room "is booked in every one of those windows"; Ember's Hiring panel ends 13:30. |
| 13 | inventory_04_rebalance_two_sites \| 6 | field_order_reverse | misread_field | yes | Same slip as #11: "ATL had 70 available and now has 25"; true figures 50 and 5, breaching safety stock. Never re-checked. |
| 14 | records_01_latest_verified \| 2 | key_naming_synonym | no_action | plausible | "CRM-2044 carries a `gdpr_request` label, and I want you to confirm before I merge it." Plan was correct in every detail. |
| 15 | scheduling_01_vc_room_for_five \| 5 | description_paraphrase | other | no | Missed the 13:00-14:30 window; "only two free 60-minute windows, both of which clash". |
| 16 | records_01_latest_verified \| 9 | key_naming_synonym | no_action | plausible | "I want a person to confirm before I merge it" over the `gdpr_request` label; correct merge plan laid out and not executed. |
| 17 | inventory_02_split_sources \| 6 | description_paraphrase | rule_misapplied | no | Read "needs exactly 55 available units" as "transfer 55 units"; took 40 from DAL and 15 from PHX instead of 40 + 5. Says so itself: "I read '55 available units' as 55 units transferred in". |
| 18 | scheduling_01_vc_room_for_five \| 5 | field_order_reverse | other | no | Self-contradictory: states everyone is busy "10:30-13:00 and 14:30-16:00", then concludes the only windows are 09:30 and 16:00. |
| 19 | scheduling_01_vc_room_for_five \| 3 | format_xml | other | no | Missed the 13:00-14:30 window. Bookings and calendars quoted correctly from XML. |
| 20 | scheduling_01_vc_room_for_five \| 2 | key_naming_synonym | other | no | Missed the 13:00-14:30 window; offered Dune/Cedar/Quay/Maple at 09:30 without video instead. |
| 21 | scheduling_01_vc_room_for_five \| 9 | description_paraphrase | other | no | Missed the 13:00-14:30 window; "09:30-10:30 has no suitable room that is free, so 16:00-17:00 is the only realistic slot". |
| 22 | scheduling_01_vc_room_for_five \| 7 | description_paraphrase | other | no | Found all four candidate starts, then: Ember "is booked 09:00-10:30, 12:00-13:30 and 15:00-17:00, so it conflicts with every candidate start". A 13:30 start does not conflict with a booking ending 13:30. |

All 13 `other` rows are the same mechanism, which the labels do not name: a constraint-reasoning slip that concludes the task is infeasible, after which the model writes an explanation instead of calling `book_meeting`.

## 2. Counts

By label:

| label | n |
|---|---|
| other (false infeasibility in scheduling) | 13 |
| no_action (paused over `gdpr_request` tag) | 6 |
| misread_field (safety_stock taken for reserved) | 3 |
| rule_misapplied | 1 |
| vocab_mapping / wrong_tool / wrong_argument / decoy | 0 |

By caused-by-perturbation:

| caused | n | episodes |
|---|---|---|
| yes | 3 | the three inventory field_order_reverse failures |
| plausible | 4 | the four records_01 key_naming_synonym failures |
| no | 16 | all 12 scheduling_01, scheduling_04, both records_03, inventory_02 |

No episode involves a wrong tool, a wrong argument, a decoy record, or a renamed key mapped to the wrong parameter. `vocab_mapping`, the mechanism the synonym condition was designed to probe, never occurred: in every synonym episode the model wrote `person_id`, `room_id`, `record_id` and `from_warehouse_id` correctly while reading `person_ref`, `space_ref`, `rec_ref` and `site_ref`.

## 3. Patterns

**scheduling_01 is a borderline task, not a presentation-sensitive one.** All 12 failures are identical in shape: seven tool calls in one batch, then a single final message declining to book. Seven of them miss the 13:00-14:30 window that every calendar leaves open; five find it but treat Ember's booking ending at 13:30 as blocking a 13:30 start. Nothing in any failure touches the perturbed element: the times, rooms and bookings are quoted correctly under reversed keys, synonyms and XML, and six of the twelve failures are under `tool_order_reverse` and `description_paraphrase`, where the observations are byte-identical to baseline. The failure rate is a flat 20-40% in every perturbed cell (12/50 pooled, 24%). Given that pooled rate, a clean 0/10 at baseline has probability about 0.09 (hypergeometric), so the "fails everywhere except baseline" pattern is consistent with a lucky baseline draw. One confound worth noting: the ten baseline runs were launched as the first burst (18:56:41-18:56:56) before any perturbed run, so a time-ordered drift cannot be excluded either. The honest reading is that Haiku solves this five-person interval puzzle in one pass about three times in four, and the perturbation grid happened to sample the misses. The same slip explains the lone scheduling_04 failure (#6), where a valid 13:30 Ember slot was declared to break the blackout rule.

**records_01 under key_naming_synonym is a caution effect, and the task design invites it.** All four failures contain a fully correct merge plan (survivor CRM-2001, email and address from CRM-2044, phone unchanged) followed by a refusal to execute until a human confirms, because the record to be archived carries a `gdpr_request` tag. That tag is seeded filler from `_decorate` in `tasks/records.py`, and it lands on the loser record in exactly two tasks, records_01 and records_03, which are exactly the two records tasks that ever fail. All 60 records_01 runs mention the GDPR tag; 56 proceed and flag it afterwards, 4 stop. The concentration is unlikely to be noise (P that all 4 of 60 failures land in one named cell of 10 is about 0.0004), so the synonym condition does seem to raise the pause rate. But the transcripts show no misread or mis-mapped key; the model reads `label_set`, `survivor_ref` and `confirmed_on` correctly and reasons from them. The most we can say is that the unfamiliar vocabulary shifts the model's judgement from "merge and warn" to "ask first". Two consequences for the write-up: this is not the HKUST-style harness-fit failure, and the grader is scoring a defensible pause as a failure. records_03 shows the same pause at baseline (run 8) and under two perturbations (1 each), which is why those two flips are classed as noise. Removing `gdpr_request` from the loser's seeded tags, or stating in the task that no exception applies, would make both cells measure merging rather than caution.

**The three inventory field_order_reverse failures are the one clean perturbation effect.** In baseline order each stock row reads `warehouse_id, bin_location, on_hand, inbound_units, reserved, unit_cost, safety_stock, ...`; reversed, `safety_stock` becomes the first numeric field and `on_hand` the last. In all three failures the model computed "available" as on_hand minus safety_stock instead of on_hand minus reserved (GAD-550@BOS 34-6=28 rather than 34-4=30; WID-410@ATL 80-10=70 rather than 80-30=50), while quoting the safety-stock rule correctly and applying it as a separate check. No other condition produced this slip in 100 inventory_03/04 runs (P of all three landing in the two field-order cells by chance is about 0.004). This is a `misread_field` in the strict sense, caused by field order, and it is the only evidence in the 23 episodes for H3. The remaining inventory failure (#17) is an instruction misreading the model itself flags, independent of presentation.

## 4. Quotes for the write-up

1. scheduling_01, field_order_reverse (#18), the self-contradiction that drives all twelve scheduling flips: "Everyone is busy 09:00-09:30, 10:30-13:00 and 14:30-16:00. The only 60-minute windows on the hour or half hour where all five are free are **09:30-10:30** and **16:00-17:00**."

2. inventory_04, field_order_reverse (#11), the one perturbation-caused mechanism, in the model's own words after re-reading the stock: "I had miscalculated ATL's available units earlier (I used 70 instead of 50)." ATL's row read `safety_stock: 10, ... reserved: 30, ... on_hand: 80` in that order.

3. records_01, key_naming_synonym (#8), a correct plan withheld: "I haven't made any changes. CRM-2044 is labelled `gdpr_request`, and merging it would copy its data into another record and archive it. I'd rather you confirm that first." Compare the baseline run 0, same data: "I merged and archived it as the policy directs, but the GDPR request may need handling by data governance."
