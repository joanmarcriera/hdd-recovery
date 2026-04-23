# First Imaging Plan: 160 GB Hitachi Source

Purpose:
- shut down cleanly
- remove the currently attached 8 TB HGST ZFS member from the motherboard SATA port
- attach an old 160 GB 5400 RPM Hitachi HDD as the new source
- image that source disk to the mounted 16 TB destination disk
- perform later analysis on the image only

## Physical swap

1. Shut the machine down cleanly.
2. Physically disconnect the current 8 TB HGST `/dev/sdb` ZFS member.
3. Connect the 160 GB Hitachi to the same motherboard SATA port if possible.
4. Leave the 16 TB Toshiba destination disk connected as it is.
5. Boot the machine.

Notes:
- Do not wipe, repartition, or alter the 8 TB HGST disk.
- Do not assume the new Hitachi will keep the same `/dev/sdX` name until verified.
- If it uses the same motherboard SATA port, the by-path may still be `pci-0000:00:17.0-ata-2.0`, but this must be checked after boot.

## Verification after boot

Run:

```bash
lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MODEL,SERIAL,ROTA,TRAN,STATE
ls -l /dev/disk/by-path
udevadm info --query=property --name=/dev/sdX | egrep 'DEVNAME=|ID_MODEL=|ID_SERIAL=|ID_PATH='
findmnt /mnt/recovery16tb
df -h /mnt/recovery16tb
lsblk -o NAME,MOUNTPOINT /dev/sdX
```

What must be true:
- the old 8 TB HGST is no longer present
- the new source is clearly identified as the 160 GB Hitachi by model and serial
- the 16 TB destination remains mounted at `/mnt/recovery16tb`
- no partition under the Hitachi source is mounted

If any partition under the Hitachi is mounted, stop and unmount it before imaging.

## Create the job config

Copy the template:

```bash
cp /root/hdd-recovery/bin/ddrescue-job-template.conf /root/hdd-recovery/jobs/hitachi160-first.conf
```

Fill in at minimum:
- `JOB_NAME`
- `DATE_TAG`
- `SOURCE_DEV`
- `SOURCE_BY_PATH`
- `SOURCE_MODEL`
- `SOURCE_SERIAL`
- `SOURCE_SIZE_BYTES`
- `BASENAME`

Recommended basename pattern:

```text
YYYYMMDD_HITACHI_<model>_<serial>_<sdX>
```

Expected output paths:
- image: `/mnt/recovery16tb/recovery/images/<basename>.img`
- mapfile: `/mnt/recovery16tb/recovery/logs/<basename>.map`
- event log: `/mnt/recovery16tb/recovery/logs/<basename>.events.log`
- rate log: `/mnt/recovery16tb/recovery/logs/<basename>.rates.log`

## Preview before reading the source

```bash
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/hitachi160-first.conf plan
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/hitachi160-first.conf first
```

Confirm in the preview:
- source device is the Hitachi
- image path is under `/mnt/recovery16tb/recovery/images/`
- mapfile path is under `/mnt/recovery16tb/recovery/logs/`

## Start the monitor

```bash
/root/start-recovery-monitor.sh
```

## First pass

For the first read, use the native easy-data-first ddrescue pass only:

```bash
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/hitachi160-first.conf first --run
```

This uses GNU `ddrescue -n`.

## Check what remains unread

```bash
/root/hdd-recovery/bin/ddrescue-status.sh /mnt/recovery16tb/recovery/logs/<basename>.map
```

## Later passes if needed

Only if unread areas remain:

```bash
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/hitachi160-first.conf retry --run
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/hitachi160-first.conf reverse --run
/root/hdd-recovery/bin/ddrescue-run.sh /root/hdd-recovery/jobs/hitachi160-first.conf retrim --run
```

## Safety reminders

- do not mount the Hitachi before imaging
- do not browse files on the Hitachi directly
- do not run SMART self-tests during imaging
- analyze the resulting image file, not the original disk
- preserve the ddrescue mapfile and logs

## After reboot / after disk swap

Provide these outputs for safe identification of the new source:

```bash
lsblk -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINT,MODEL,SERIAL,ROTA,TRAN,STATE
ls -l /dev/disk/by-path
```

Then fill the job config and preview it before any real imaging command.
