# Tool Evaluation 2026-04-21

Purpose:
- compare additional recovery and analysis ideas against the current GNU `ddrescue` acquisition workflow
- keep the recommendation local to this machine

## Conclusion

For acquisition from old HDDs where source preservation matters more than speed:
- Keep GNU `ddrescue` as the primary imaging tool.
- `dc3dd` is useful, but not better than `ddrescue` for failing-media acquisition.
- `guymager` may be useful as an optional GUI front-end or for forensic container formats, but it is not necessary for the current low-risk raw-image workflow.
- BitcoinCarver, if later verified, belongs to the analysis stage and should run against image files, not original disks.

## GNU ddrescue

Why it remains primary:
- Native mapfile-based workflow for resumable rescue.
- Designed to read good areas first and return later for bad areas.
- Supports controlled later passes such as direct retry, reverse retry, and retrim.
- Best match for the current goal: make a full raw image first, then analyze the image instead of the source.

## dc3dd

Observed local state:
- Binary present: `/usr/bin/dc3dd`

Verified package description:
- forensic features include on-the-fly hashing, error logging, grouped error logging, progress reporting, and split output.

Assessment:
- Good secondary tool.
- Stronger than classic `dd` for forensic logging and hashes.
- Not a better primary tool than GNU `ddrescue` for degraded media because the current plan depends on mapfile-driven partial rescue and targeted rereads of only unfinished regions.
- Better fit when the source is healthy enough and the operator wants a straightforward forensic copy with integrated hashes and split output.

Recommendation:
- Keep installed and available.
- Do not replace GNU `ddrescue` with `dc3dd` for fragile HDD acquisition.

## guymager

Observed local state:
- Binary present: `/usr/bin/guymager`

Verified package description:
- supports forensic imaging with a Qt GUI
- supports multiple image formats and emphasizes usability and speed

Assessment:
- Potentially useful if a GUI workflow or EWF/AFF output is desired.
- Not necessary for the current native raw-image workflow to `/mnt/recovery16tb/recovery/images`.
- Adds another acquisition path and another set of operator choices, which is not ideal before the first high-value run unless there is a specific reason to use EWF/AFF or GUI-driven acquisition.

Recommendation:
- Optional, not primary.
- If used at all, prefer it later only after the raw ddrescue workflow is already proven on this machine.

## BitcoinCarver

Assessment:
- This is not an acquisition tool.
- If trustworthy and effective, it would belong to the post-acquisition analysis stage.
- It should operate on raw images or extracted partitions, not on original source disks.

Status of verification:
- I could not verify a clear trusted upstream source from primary documentation during this review.
- It was not found in the local Kali package search.

Recommendation:
- Not needed before acquisition readiness.
- Do not install or rely on it yet.
- Revisit only after the first image is secured, and only after verifying provenance, maintenance state, and exact scan behavior.

## Practical workflow decision

Preferred order:
1. Acquire a raw full-disk image with GNU `ddrescue`.
2. Preserve the mapfile and logs.
3. Analyze the image only.
4. Add specialized wallet-carving or picture-processing tools later if needed.

Reason:
- acquisition mistakes are harder to undo than analysis mistakes
- minimizing moving parts during the first imaging run reduces operator risk
