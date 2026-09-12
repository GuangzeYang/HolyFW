# dataset-extract / export-evtx

Offline slicer for a **mixed domain SPAN** pcap. It writes **new** malicious and benign pcapng files plus optional per-task evtx windows. The source mixed pcap is **read-only**.

```powershell
python -m dataset_processor extract ...
python -m dataset_processor export-evtx ...
dataset-extract
dataset-export-evtx
```

`attacker extract` is a thin wrapper around extract only.

## 0. Linux: preprocess evtx on Windows first

Linux has no `wevtutil`. On Windows, export Sysmon (and optional Security) to XML, copy the XML + pcap + task `.md` to the server, then extract with `--evtx *.xml`.

Windows (needs `wevtutil`; does **not** read the pcap):

```powershell
python -m dataset_processor export-evtx `
  --evtx 20260911-20260912\attacker\sysmon_2026-09-11.evtx `
  --security-evtx 20260911-20260912\attacker\security_logon_2026-09-11.evtx `
  --dc-security-evtx 20260911-20260912\DC\security_logon_2026-09-11.evtx `
  --out-dir 20260911-20260912\xml
```

Writes `{parent}_{stem}.xml` so attacker and DC `security_logon` files do not overwrite each other. Sysmon query is EID 1 or 3; Security files are exported in full. XML can be hundreds of MB. Copy to Linux:

- `attacker_sysmon_2026-09-11.xml`
- mixed SPAN pcap
- `attacker/2026-09-11/*.md`

Linux (needs `tshark` + `mergecap`; no wevtutil):

```bash
python -m dataset_processor extract --date 2026-09-11 \
  --evtx attacker_sysmon_2026-09-11.xml \
  --pcap 20260911-20260912.pcap \
  --transcripts-dir ./2026-09-11 \
  --out-dir ./extracted
```

Do not pass `.evtx` or `--security-evtx` on Linux. Per-task Security slices still need Windows `wevtutil epl`.

## 1. Extract inputs

Pass paths explicitly. The module does not search for evtx or pcap files.

| Argument | Role |
| --- | --- |
| `--pcap` | Mixed SPAN `.pcap` / `.pcapng` (**never modified**) |
| `--evtx` | Attacker Sysmon `.evtx` (Windows) or wevtutil `.xml` (Linux) |
| `--date YYYY-MM-DD` | Batch: one output set per `{task_id}.md` in `--transcripts-dir` |
| `--transcripts-dir` | Directory of attacker task markdown (default: `--out-dir` if it contains `.md`) |
| `--out-dir` | Directory for **new** files only |
| `--security-evtx` / `--dc-security-evtx` | Optional Security evtx, sliced by task time window (**Windows wevtutil only**) |
| `--attacker-ip` | Attacker host IPv4 for scan ICMP/ARP/bare SYN (else config or inferred from EID 3) |

`--out-dir` must not resolve to the same file as `--pcap`. If it would, the command exits with an error.

Tasks whose display filter matches nothing (`frame.number == 0`) **skip tshark** and do not write a pcap.

## 2. What extract writes

Under `--out-dir`:

- `{task_id}_{technique}.pcapng` — that task's **malicious** copy (complete TCP streams + UDP + scan probes)
- `{task_id}_{technique}_{Sysmon,Security,DC_Security}.evtx` — optional time-window slices (Windows)
- `benign.pcapng` — mixed minus malicious, TCP only if the stream has a handshake (`syn`) **and** teardown (`fin` or `rst`)
- `tuples.json` — Sysmon 5-tuples, with `tcp_complete`

Incomplete TCP is **omitted from these outputs**. Those packets remain in the original mixed pcap.

`discovery.host-scan` / `discovery.port-scan` still keep attacker-sourced ICMP, ARP, and bare SYN in the task window even when those TCP streams have no FIN/RST.

## 3. Example (calendar day 2026-09-11, all on Windows)

```bash
python -m dataset_processor extract --date 2026-09-11 \
  --evtx 20260911-20260912/attacker/sysmon_2026-09-11.evtx \
  --pcap 20260911-20260912/20260911-20260912.pcap \
  --transcripts-dir 20260911-20260912/attacker/2026-09-11 \
  --out-dir 20260911-20260912/extracted \
  --security-evtx 20260911-20260912/attacker/security_logon_2026-09-11.evtx \
  --dc-security-evtx 20260911-20260912/DC/security_logon_2026-09-11.evtx
```

Flags-only form (no `extract` subcommand) still works. Optional `--config attacker/config.json` supplies `extract.lab_nets` and `extract.attacker_ip`.

## 4. Tools

Extract needs **tshark** and **mergecap**. `export-evtx` and per-task evtx slices need **wevtutil** on Windows.

Long benign keep-lists are split across several `tshark -Y` copies and merged with `mergecap`. Temporary `.benign.partNNNN.pcapng` files are written under `--out-dir` and deleted after merge. They are never written over `--pcap`.
