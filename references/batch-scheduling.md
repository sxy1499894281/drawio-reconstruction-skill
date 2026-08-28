# Bounded-Concurrency Batch Scheduling

Read this before dispatching a reconstruction batch with two or more images. This is an agent-executed scheduling protocol, not an automatic queue implemented by `batch_manifest.py` or a change to Codex's concurrency configuration. The manifest fixes scope and output ownership; it does not launch agents. All fidelity, role-separation, review, and optional PPTX gates in `SKILL.md` still apply.

## Logical Jobs And Progress

Create one logical job for every manifest entry, including entries waiting for capacity. One image job is not one permanent agent: it moves through inventory, icon production/review when applicable, reconstruction production/review, and the parent's final visual audit. Every FIX inserts a fresh repair producer and then a fresh independent reviewer for the changed version. No fixed number of rounds applies.

Keep scheduling state in the existing task/batch notes or a small sidecar in the supporting-artifacts directory (use `其他产物` when that organization is requested). Do not clutter the deliverable directory with one scheduler file per event. Track:

- manifest entry id, unchanged reference, exclusive output paths, and whether PPTX was requested;
- current phase and state: `queued`, `running`, `waiting_capacity`, `review_pending`, `repair_pending`, `accepted`, or `user_stopped`;
- literal agent ids, role, owning coordinator, capacity reservations, artifact version/hashes, result paths, next required action, and any capacity rejection;
- the actual limit, whether the primary counts, which other occupants count, and whether finished/idle threads still consume slots.

`accepted` is reconstruction acceptance only after all applicable independent gates and the parent's final visual audit pass. It is not completion of a pending PPTX request. A capacity wait is not a quality failure, skip, or completed entry. Keep accepted artifacts available while other images continue.

## Capacity Accounting And Admission

Use the current runtime/tool instructions and observable tool responses as the authority. Limits may count the primary differently or count open threads rather than only running agents. Normalize these semantics before planning. A configuration value alone does not prove free capacity. Count nested agents and other in-scope or unrelated occupants whenever the runtime counts them. Do not assume a finished or idle thread has released its slot.

Maintain a single admission ledger at the parent. Let `free` mean the currently available counted slots after occupants and outstanding launch reservations. Reserve a slot before dispatch and reconcile the reservation with the actual launch response. Do not let multiple image coordinators independently spend the same free slot. An unknown limit is not permission for unlimited spawning: start conservatively, update from runtime evidence, and never probe by launching disposable agents.

Choose a mode that can make forward progress:

1. **Direct phase dispatch (preferred under tight capacity):** The parent schedules fresh one-image, one-role specialist agents directly; they do not spawn children. Use a scoped inventory worker for detailed image analysis if the inventory is not ready. That worker may write the inventory/style notes but cannot create icon assets or a `.drawio` file. Then dispatch the separate producers and read-only reviewers required by `SKILL.md`. Run eligible phases for different images concurrently. The parent handles coordination and final visual audit, not asset/diagram production or substitute acceptance. No resident per-image coordinator is required.
2. **Resident image coordinators (only when budgeted):** Each owns one logical image job and requests a parent-issued slot lease before launching each specialist. Admission must leave at least one shared specialist slot available or reserved for an in-flight producer/reviewer; do not count that same slot as capacity for another coordinator. When it frees, give the next eligible specialist the lease. The parent may also use that shared slot for a direct phase assignment on a queued image without creating another coordinator; record who coordinates each image. Coordinators must not recursively spawn more coordinators. If the runtime cannot enforce/communicate these leases, use direct phase dispatch instead.

For example, with four total slots **including the parent** and no other occupants, direct dispatch allows at most three live phase agents across images. Resident mode allows at most two image coordinators plus one shared specialist, so it has less useful concurrency here. Prefer direct dispatch. These are examples, not hard-coded limits.

With only one usable child slot, run fresh specialist agents sequentially: inventory, Icon Producer, distinct Icon Reviewer, fresh Reconstruction Producer, distinct Reconstruction Reviewer, with fresh repair pairs as needed. Announce this capacity-limited mode; it retains all standalone quality gates and is not a silent fallback to parent-only reconstruction. With no usable child slot, preserve the queue and report pending capacity. Never raise global limits, change models/reasoning settings, or cancel other tasks to create room without user authorization.

## Rolling Dispatch And Slot Lifecycle

1. Build the full manifest and queue before detailed reconstruction. Before each launch, inspect current completion/capacity information and update the ledger.
2. Prefer ready independent reviews and requested repairs, then continuations of admitted images, then new images in manifest order. Apply a fairness override across those priorities: after a complete repair/review pair for one image, if another image has a ready phase, dispatch at least one such phase of the longest-waiting other image before starting another repair pair for that image. Measure waiting age from when the current phase became ready, not the image's original admission time. If no other phase is ready, continue the remaining repair without an artificial wait. This also applies with only one usable child slot. Yield only between completed phases; never interrupt active work or cap repair rounds. With multiple slots, other images keep using them while a difficult image is repaired; do not put a batch-wide barrier around its repair loop.
3. Dispatch only a ready phase whose dependencies passed, whose files are not being written/reviewed by another phase, and whose slot is reserved. Give each agent exactly one image, a bounded role, and exclusive producer paths or read-only reviewer paths. Never overlap production and review of the same version.
4. On terminal completion, persist the literal result and version/hash evidence before releasing the owned agent handle. If the runtime requires an explicit close/release of a **finished** thread to reclaim capacity, use only an available lifecycle tool for that completed task. Do not invent a close tool or use interruption as a release substitute. Never close, interrupt, replace, or abandon an active producer/reviewer merely to free capacity or because it is slow. Never reuse completed producer/reviewer identities.
5. Advance the image only after validating the result under `SKILL.md`. Immediately fill an actually available slot with the next eligible phase/image; do not wait for every image in an arbitrary fixed-size wave to finish. If other work is still running, it continues unchanged.
6. On a capacity rejection, retain that phase in `waiting_capacity`, remove its unfulfilled launch reservation, and record the actual error. Preserve current artifacts and completed gates. Reconcile tool state before retrying; if a launch outcome is ambiguous, check for the created agent before risking duplicate writers. Do not tight-loop launch attempts. Wait for completion or another relevant state change, using non-destructive waits/status checks and concise progress updates.
7. If finished threads still occupy the limit and the runtime offers no safe release mechanism, report the pending phases and the exact lifecycle limitation. Do not claim the queue can drain automatically in that environment, reuse a completed agent to fake a fresh identity, or remove required roles. Preserve results for resumption. An external capacity change can resume the queue without rebuilding accepted images.

Only user cancellation or a confirmed unrecoverable inability to continue changes the task's stopping behavior; a transient capacity rejection by itself does not. Do not stop the whole batch just because one slot request was rejected. Conversely, do not claim completion when required independent reviewers could not run.

## Quality And Delivery Invariants

- Fewer concurrent images means longer elapsed time, not a relaxed acceptance standard. Never reduce the inventory, visual fidelity, export resolution, icon/placement review sheets, model/reasoning setting, or number of necessary repair rounds to fit the limit.
- Every applicable gate still has distinct fresh producer/reviewer identities and artifact-bound evidence. Only the proper independent reviewer can return PASS. A coordinator, producer preflight, XML check, or prior-version verdict cannot replace it.
- Resume from the latest preserved artifacts and pending phase. Do not rebuild successful images or restart from the reference after a capacity wait.
- Final batch acceptance still requires batch technical checks and the parent's comparison of **every** exported preview to its unchanged reference. Parent findings trigger the same fresh repair/review pair.
- If PPTX was requested, wait until all requested reconstructions pass their reviews, then follow `drawio-to-pptx.md`, including the PowerPoint/WPS compatibility profile. Count any conversion agents against the same limit; do not start conversion by dropping or bypassing unfinished reconstruction reviews.
- Report accepted, queued/waiting, review/repair-pending, user-stopped, and genuinely skipped entries accurately. Explain scheduling constraints without promising identical visual outputs or compatibility tests that have not been performed.

## Scheduling Examples

| Situation | Required action |
| --- | --- |
| 12 images; 4 total slots including parent | Queue all 12; use at most 3 direct phase agents. When a completed phase actually releases capacity, start its next role or another eligible job; keep the other agents running. All 12 retain full independent reviews. |
| 12 images; only 1 usable child slot | Dispatch fresh stage agents one at a time; keep all other phases queued and announce the throughput constraint. |
| One image receives FIX while another passes | Enqueue the repair/review pair for the failed version; preserve the accepted image and let other slots continue. |
| One image repeatedly receives FIX with only 1 usable child slot | After each repair/review pair, give the longest-waiting other image a ready phase before the next repair pair. Keep the difficult image pending, not accepted or abandoned. |
| Spawn returns a capacity error | Keep the exact pending phase and artifacts; reconcile reservations, wait for a capacity change, then retry. Do not skip the reviewer. |
| Completed threads still count and no release tool exists | Record the lifecycle blockage and unfinished phases; preserve outputs and request the necessary environment/user action rather than killing active agents or fabricating PASS. |

For product background, the [official Codex sub-agent configuration documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents) describes limits on open spawned threads that exclude the primary. Actual runtime/tool instructions can expose different counting semantics; always follow the environment in use.
