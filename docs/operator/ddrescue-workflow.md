# GNU ddrescue Workflow

This machine now uses GNU `ddrescue 1.30` as the standard acquisition tool.

Goal:
- capture a full raw image first
- keep a persistent mapfile from the first command onward
- rescue easy sectors first
- return later for remaining unread areas in controlled passes
- analyze the image, not the original disk

Native pass sequence used here:

1. First pass
   - command behavior: `ddrescue -n`
   - purpose: get the easy readable data quickly without retry work
   - wrapper: `ddrescue-run.sh <job.conf> first --run`

2. Retry pass
   - command behavior: `ddrescue -d -O -r3`
   - purpose: use direct input access and a bounded number of retries on remaining unread areas
   - wrapper: `ddrescue-run.sh <job.conf> retry --run`

3. Reverse retry pass
   - command behavior: `ddrescue -d -O -R -r1`
   - purpose: approach remaining trouble spots from the opposite direction
   - wrapper: `ddrescue-run.sh <job.conf> reverse --run`

4. Optional retrim pass
   - command behavior: `ddrescue -d -O -M -r1`
   - purpose: mark failed blocks for retrimming and make one more conservative attempt
   - wrapper: `ddrescue-run.sh <job.conf> retrim --run`

What tells you what was not imaged:
- the GNU ddrescue mapfile
- `ddrescuelog -t <mapfile>` for summary
- `ddrescue-status.sh <mapfile>` for summary plus the first unfinished ranges

Recommended use:

1. Copy the template config:
   - `cp /root/hdd-recovery/bin/ddrescue-job-template.conf /root/hdd-recovery/jobs/<jobname>.conf`

2. Fill in:
   - `SOURCE_DEV`
   - `SOURCE_BY_PATH`
   - `SOURCE_MODEL`
   - `SOURCE_SERIAL`
   - `SOURCE_SIZE_BYTES`
   - `DATE_TAG`
   - `BASENAME`

3. Preview before running:
   - `ddrescue-run.sh /root/hdd-recovery/jobs/<jobname>.conf plan`
   - `ddrescue-run.sh /root/hdd-recovery/jobs/<jobname>.conf first`

4. Run the first pass:
   - `ddrescue-run.sh /root/hdd-recovery/jobs/<jobname>.conf first --run`

5. Review remaining unread areas:
   - `ddrescue-status.sh /mnt/recovery16tb/recovery/logs/<basename>.map`

6. If needed, continue with later passes:
   - `ddrescue-run.sh /root/hdd-recovery/jobs/<jobname>.conf retry --run`
   - `ddrescue-run.sh /root/hdd-recovery/jobs/<jobname>.conf reverse --run`
   - `ddrescue-run.sh /root/hdd-recovery/jobs/<jobname>.conf retrim --run`

7. Initialize the analysis database and scan structure (on the Optiplex, before transfer):
   - `bin/image-analysis-init.sh /mnt/recovery16tb/recovery/images/<basename>.img --map /mnt/recovery16tb/recovery/logs/<basename>.map`
   - `bin/image-structure-scan.sh /mnt/recovery16tb/recovery/images/<basename>.img.analysis.sqlite`

8. Transfer the image to TrueNAS for all heavy analysis:
   - `bin/send-image-to-truenas.sh /mnt/recovery16tb/recovery/images/<basename>.img <truenas-host>`
   - This rsyncs the image, SQLite database, ddrescue logs, and any existing export outputs.
   - After transfer, the script prints the exact `docker exec` command to continue analysis in the container.
   - All analysis stages beyond `image-structure-scan.sh` run in the Docker container on TrueNAS.

Notes:
- The runner refuses to proceed if any partition under the source disk appears mounted.
- The runner defaults to preview mode and requires `--run` for execution.
- `--ask` remains enabled so ddrescue prompts before it starts.
- Do not reuse a mapfile from an unrelated disk.
