# Verified State 2026-04-21

Host:
- Hostname: `optiplex7040`
- Model: Dell OptiPlex 7040
- OS: Kali GNU/Linux Rolling
- Kernel: `6.19.11+kali-amd64`
- Firmware: `1.24.0` dated `2022-07-14`

Disk identity and path map:
- `/dev/sda`
  - Model: `SAMSUNG SSD CM871a 2.5 7mm 256GB`
  - Serial: `S2YCNY0HA06310`
  - By-path: `pci-0000:00:17.0-ata-1.0`
  - Role: OS SSD
- `/dev/sdb`
  - Model: `HGST HUH728080ALE604`
  - Serial: `VLK95AYY`
  - By-path: `pci-0000:00:17.0-ata-2.0`
  - Current contents: GPT disk with `/dev/sdb1` as `zfs_member`
  - Label on `/dev/sdb1`: `zfsPool`
  - Rule: do not format, mount, or alter this disk
- `/dev/sdc`
  - Model: `TOSHIBA MG08ACA16TE`
  - Serial: `8010A0CBFVGG`
  - By-path: `pci-0000:02:00.0-ata-4.0`
  - Role: destination disk on ASM1166 PCI SATA controller

Destination verification:
- `/dev/sdc1` is mounted at `/mnt/recovery16tb`
- Filesystem: `ext4`
- Label: `RECOVERY16TB`
- UUID: `c2244b9a-26b3-4353-bc87-5be944139157`
- Mount mode observed: `rw,relatime`
- Free space observed on 2026-04-21: about `15T` available, `1%` used

Recovery directory verification:
- `/mnt/recovery16tb/recovery/images`
- `/mnt/recovery16tb/recovery/exports`
- `/mnt/recovery16tb/recovery/logs`
- `/mnt/recovery16tb/recovery/manifests`

Monitoring verification:
- `/root/start-recovery-monitor.sh` exists and is executable
- `/root/recovery-monitor/smart-lite-all.sh` exists
- `/root/recovery-monitor/tail-latest-ddrescue-log.sh` exists
- tmux session `recovery-monitor` already exists

Tool verification:
- Present: `ddrescue`, `ddrescuelog`, `ddrescueview`, `smartctl`, `iostat`, `iotop-c`, `tmux`, `pv`, `sha256sum`, `hdparm`, `lsblk`, `blkid`, `findmnt`
- Also present from `ddrutility`: `ddru_findbad`, `ddru_ntfsbitmap`

Notes:
- `smartctl -n standby -i /dev/sdb` and `smartctl -n standby -i /dev/sdc` both reported `ACTIVE or IDLE` at check time.
- No imaging was started.
- GNU ddrescue was installed after the initial verification and is now available as version `1.30`.
