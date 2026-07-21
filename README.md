<p align="center">
  <img src="assets/processx.png" alt="ProcessX logo" width="280">
</p>

# ProcessX

A lightweight Windows tool for managing per-process CPU priority, I/O priority, and CPU affinity — with automatic detection of Intel P-core/E-core and AMD CCD topology, so you can isolate processes to the right cores with one click.

## Features

- **CPU Priority** — set Idle / Below Normal / Normal / Above Normal / High / Realtime per process
- **I/O Priority** — set Very Low / Low / Normal / High per process
- **CPU affinity (CPU sets)** — pick exactly which logical CPUs a process is allowed to run on
(CPU Sets work with all known anti-cheat systems, including Easy Anti-Cheat.)
- **Topology-aware quick presets**
  - Intel hybrid CPUs: one-click "E-Cores Off" / "P-Cores Off"
  - AMD CPUs with two CCDs: one-click "CCD0 Off" / "CCD1 Off"
  - SMT On / SMT Off toggle (AMD) / HT On / HT Off toggle (Intel)
- **Rules persist automatically** — save a rule once for an `.exe` name, and it's re-applied every time that process launches, including to every running instance if there are multiple copies open
- **System tray support** — runs quietly in the background
- **Portable single-file JSON config** — rules are stored in `%APPDATA%\ProcessX\rules.json`

## Requirements

- Windows 10 or 11
- **Must be run as Administrator** for priority/affinity changes to apply to processes you don't own, and for some topology-detection APIs to work correctly
- No Python installation needed — see Download below

## Download

Grab the latest `ProcessX.exe` from the [Releases](../../releases) page. No installation, no Python, no dependencies — download and run.

> **Note:** The exe is unsigned, so Windows SmartScreen and some antivirus tools may flag it on first run ("Windows protected your PC" or a false-positive malware warning). This is common for small unsigned utilities, especially ones that touch process priority/affinity via `ctypes` — behavior that looks similar to what actual malware does. If SmartScreen blocks it, click **"More info" → "Run anyway"**. If you'd rather not trust a prebuilt binary, you can always [build it yourself from source](#building-from-source).

## Usage

Right-click `ProcessX.exe` → **Run as administrator**.

- The main window lists running processes. Double-click one (or use "Add Rule") to open the rule editor.
- Set CPU priority, I/O priority, and CPU affinity, then **Apply & Save**.
- The rule is saved by process name (e.g. `game.exe`) and automatically re-applied to that process every time it starts — you don't need to keep ProcessX open in the foreground once a rule exists, but the background watcher (which auto-applies rules to newly launched processes) does need the app running.

### Command-line flag

```bash
ProcessX.exe --apply-rules
```
Applies all saved rules to currently running processes and exits immediately — useful for a scheduled task or startup script instead of running the full GUI.

## Building from source

Only needed if you want to modify the code yourself; most users should just use the prebuilt `.exe` above.

Requirements: Python 3.8+, [psutil](https://pypi.org/project/psutil/), and optionally `pystray` + `Pillow` for system tray support.

```bash
git clone https://github.com/jadenpeek/ProcessX.git
cd ProcessX
pip install psutil pystray pillow
python ProcessX.py
```

## How CPU topology detection works

On startup, ProcessX tries to identify your CPU's core layout so it can offer relevant quick-select buttons:

- **Intel**: uses `GetLogicalProcessorInformationEx` to check whether Hyper-Threading is enabled, and `GetSystemCpuSetInformation` (plus a built-in table of known hybrid CPU model numbers) to determine the P-core/E-core split.
- **AMD**: assumes a second CCD exists if the CPU reports 16+ logical CPUs (a heuristic based on common Ryzen chiplet layouts — not a guaranteed-accurate topology read on every SKU).

If detection fails or your CPU isn't hybrid/multi-CCD, the relevant quick buttons simply won't appear — manual per-CPU checkboxes always work regardless.

## Notes & limitations

- Rules match on **process name only**, not full path. Two different programs sharing an executable name will share the same rule.
- A rule applies to **every running instance** of a matching process name, not just one.
- Realtime CPU priority can starve other processes, including OS-critical ones — use with caution.

## Contributing

Issues and pull requests are welcome. If you're adding support for a new CPU family or fixing a topology-detection edge case, please include the CPU model and a brief description of what was misdetected.

## License

MIT — see [LICENSE](LICENSE).
