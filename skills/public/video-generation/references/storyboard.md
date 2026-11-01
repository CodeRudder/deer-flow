# Storyboard design images (分镜路径)

Loaded from SKILL.md at three points: (1) before the input table shows or
updates the 生成分镜效果图 row as 是, (2) at routing time on the storyboard
path (applicability + the prompt-declares-first exception), (3) before
dispatching any storyboard image call (SKILL.md Step 2.5). This file owns the
whole storyboard path from input-table settlement to the video call.

## 1. Input-table settlement (at the gate, never deferred)

- **Storyboard-image intent = 是 (one exception to no-field-interrogation)**:
  when the user sets the 生成分镜效果图 row to 是 (or asks for storyboard
  design images in their words), the intent AND its limits must be settled at
  THIS gate, before moving on — never deferred to the plan card:
  1. **Cost note in the options/question text** (the only sanctioned place —
     the plan card itself still never shows quota or cost notes): storyboard
     images consume image-generation quota, one image per shot.
  2. **Estimated shot count**: take the duration's shot-budget band ceiling
     (shot budget table — provider-independent; 5 s default → 4–6 s band → 2
     shots) and TELL the user the duration–shot linkage ("5 s supports at most
     2 shots; storyboard work reads better at 7 s or longer").
  3. **On-the-spot limit check** with the known inputs: estimated shots (=
     storyboard image count) + originals expected to be ATTACHED to the video
     call (default 0 — originals are not passed unless they carry information
     the storyboard images don't cover or the user explicitly requires
     likeness to a specific photo; count them only when the user says they
     join the call). Check against the routed provider's reference cap
     (H3 9 / Seedance 2.5 30 via `--describe-provider`; provider here is the
     predicted one — see SKILL.md Choosing the mode for how the provider
     resolves) AND the H3 material budget (storyboard images + attached
     originals ≤ 5 — H3 only; Seedance caps at its reference cap). Over the
     limit → the user chooses on the spot: fewer shots / drop attached
     originals / switch to a provider that supports the plan / drop the
     storyboard intent. State the chosen resolution in the 生成分镜效果图 row
     and re-show the table.
  4. The prompt-writing step (SKILL.md Step 2) re-checks the finalized shot
     count against this settled budget; over → back to the user with the SAME
     options (this is conversational adjustment — no gate, no card; holding
     out has no "force through" exit: staying over the limit means dropping
     the storyboard intent).

## 2. Applicability (decide at routing time, not on the plan card)

**Storyboard-image applicability:** when the 生成分镜效果图 row = 是, reuse
the input-table `--describe-provider` run (re-run if the routed provider
changed). Every reference-capable provider supports storyboard images — the
only checks are reference mode in the manifest (T2V / frame modes / H3-Max
fail this) and the caps settled at the input table (reference cap + H3
budget). Seedance 2.0 family lacks official storyboard guidance, so alignment
there is weaker — note that on the plan card when routed there. Not
applicable → tell the user IMMEDIATELY (before any prompt writing) and offer:
switch providers / drop the storyboard intent. If the routed provider differs
from the input-table prediction, re-run the limit check under its numbers and
tell the user. Silent ignoring is forbidden.

## 3. The video prompt declares the design images before they exist

**Storyboard-path exception:** the storyboard
design images are the one planned-but-unseen asset the prompt MAY define in
advance — the prompt is written BEFORE the storyboard images exist, declaring
each image's shot role (format rules in the provider's storyboard section:
`references/providers/{minimax|seedance}/prompt-format*.md`). User-supplied
references must still come from actual files. The storyboard images are
generated right after prompt writing (SKILL.md Step 2.5 — the step below) and
confirmed verbatim on the plan card.

## 4. Generating the images (SKILL.md Step 2.5)

Run this step ONLY when the 生成分镜效果图 row = 是 passed all checks
(input-table intent + routing-time applicability + limit check + shot-count
re-check + the SKILL.md Step 2 video prompt file already written). Generated
BEFORE the plan card so the card confirms real files.

**Generation rules:**

- **First shot** — by the 素材 row's collection intent:
  - 有图 or 无图帮我找 (collected) → `image-editing` with the user's /
    collected material as the base image (identity/consistency anchor;
    redraw workaround active → the design sheet replaces the photo as the
    shot-1 anchor and ALWAYS joins the video call as the identity anchor;
    in storyboard images only the character keeps the sheet's illustration
    form — scene, props, and products render realistically — a
    realistic-face storyboard is rejected by Seedance even when the theme
    demands live action (rule and evidence: `references/workarounds.md`)):
    ```bash
    python /mnt/skills/public/image-editing/scripts/edit.py \
      --image {base material path} \
      --prompt {first-shot description: framing, subject pose/action, scene; keep identity, style, palette} \
      --output-file /mnt/user-data/outputs/{name}-sb-1.png \
      --quality medium
    ```
  - 无图且不找 → `image-generation` text-to-image fallback (the input-table
    approval covers this; do NOT re-ask). Quality is pinned via env inline
    (`OPENAI_IMAGE_QUALITY=medium` — the image-size map stays untouched; the
    size drop is NOT done via `OPENAI_IMAGE_MODE=chat_completions`, which
    changes the response protocol):
    ```bash
    OPENAI_IMAGE_QUALITY=medium python /mnt/skills/public/image-generation/scripts/generate.py \
      --prompt-file /mnt/user-data/workspace/{name}-sb-1.json \
      --aspect-ratio {target ratio, default 16:9} \
      --output-file /mnt/user-data/outputs/{name}-sb-1.png
    ```
  By default the storyboard images are the ONLY reference assets passed to
  the video call — on the redraw path the design sheet joins as well
  (`references/workarounds.md`); original materials join only when they
  carry visual
  information the storyboard images don't cover or the user explicitly
  requires likeness to a specific photo (the card then lists them as
  attached). One image per shot; EVERY subsequent shot chains from the
  FIRST shot's image via `image-editing` (change only
  景别/动作/机位/场景; identity, clothing, palette, style stay anchored to
  shot 1) — never from the previous shot (previous-shot chaining forces
  serial generation and compounds drift). **Dispatch shots 2..N in
  parallel**: once shot 1 succeeds, launch all remaining shots in ONE
  foreground bash call with `&` + `wait` — shell backgrounding is the
  reliable parallel form; independent tool calls may not actually run
  concurrently. Never `&&`-chain shots (a short-circuit would still bill
  reserved calls); `;` is sequential, not parallel.
- **Prompt-file JSON** (image-generation only): `{ "prompt": "..." }` — the
  shot description verbatim (subject, framing, action, scene, style; no
  storyboard jargon inside the image prompt).
- **Ratio**: generate at the plan's output ratio (default 16:9, explicit user
  ratio wins). image-editing has no ratio parameter — the output follows the
  base image; if the base image's ratio conflicts with the target, the
  storyboard image lands on the nearest in-spec ratio with the same
  orientation (`check_materials.py` preflight still applies; it validates, it
  never crops to a target). State the landed ratio on the plan card if it
  differs from the target.
- **Failure**: shot 1 failed → dispatch nothing further, report, and wait.
  Dispatched shots failed → collect every result, keep the successes, report
  the failed shot(s), and ask the user whether to regenerate them. No
  self-retry (each attempt consumes image quota even on failure); a retried
  image costs one more image call.
- **Resume**: on a new run with the storyboard half-generated, reuse existing
  on-disk storyboard files (`{name}-sb-N.png`), generate only the missing
  shots.
- After all images exist, run the spec preflight (`check_materials.py` with
  the routed provider) on them; on the redraw path also inspect every
  storyboard both ways — the character's illustration form stays
  recognizable and scene, props, and products stay realistic; flag either
  drift on the plan card for the user's regenerate decision — then proceed
  to the plan card.

## 5. Plan-card integration

**Plan-card integration:** the storyboard images appear in the plan card's
Materials row (source-labeled; when originals are not passed the card notes
"original materials used only to generate the storyboard images"), and the
分镜 row lists them shot-by-shot (shot no. ↔ timestamp ↔ path) plus the
usage declaration: passed in shot order as reference images, for composition
and content only — their art style and any in-image text are NOT adopted.
The plan card is a PRODUCT check (look at the images, then approve) — intent
and limits were already settled at the input table.

Redraw-workaround path: the Materials-row disclosure for redrawn design
sheets is specified in `references/workarounds.md`.

## 6. Adjustments from the plan card

**Storyboard-image
adjustments use affected-only regeneration**: regenerating one shot keeps
the other shots (each attempt costs one image call); changing the shot
count redoes only affected shots (fewer shots → discard the surplus
images; more shots → generate only the new ones); regenerating shot 1 or
changing the original materials invalidates every downstream shot derived
from them — regenerate the chain. Dropping the storyboard intent on the
card discards the storyboard images, reverts the prompt to the plain
reference formulation, re-routes, and re-shows the card.

## 7. The video call (SKILL.md Step 4)

**Storyboard path**: the storyboard images were generated in
SKILL.md Step 2.5 (the step above) and confirmed on the card — pass them in
the order the prompt declares (originals only when the card lists them as
attached), `--aspect-ratio` = the card's concrete ratio, and generate
immediately on confirmation.
