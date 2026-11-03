<p align="center">
  <img src="assets/processx.png" alt="ProcessX logo" width="280">
</p>

# ProcessX

A small Windows tool I made to set CPU priority, I/O priority, GPU priority and CPU Sets for individual programs, and apply them every time the program starts.

## What it can do

- Set CPU, I/O and GPU priority per program
- Pick exactly which logical CPUs a program may use (CPU Sets)
- One-click presets for your CPU: E-Cores Off / P-Cores Off, CCD0 Off / CCD1 Off, SMT/HT Off.
- Live usage bar for every core, so you can see your unticked cores stay idle
- Runs in the system tray, and rules can be imported and exported

## Download

Grab the latest `ProcessX.exe` from the [Releases](../../releases) page. There's nothing to install, just download and run. It asks for administrator rights when it starts.

## How to use it

1. Start ProcessX.
2. Double-click a process in the list, or press **Add Rule** and type an exe name like `game.exe`.
3. Choose the priorities and the CPUs, then press **Apply & Save**.

The rule is saved by exe name and applied every time that program starts. ProcessX has to be running for that (the tray is fine), because a small background watcher is what catches new programs as they launch.

## Task Manager won't show your CPU Sets

This confuses a lot of people, so I'll say it up front. **Task Manager will never show that CPU Sets were applied.** Its "Set affinity" window reads the old affinity mask, and CPU Sets are a different mechanism that doesn't touch that mask. So after you apply a rule, Task Manager will keep showing every core ticked, and that's normal.

To check that your rule works, use the **CPU Cores** view in ProcessX. The cores you unticked are dimmed, and if you load the program you should see them stay idle while the allowed ones do the work.


## What should I actually apply?

Honest answer: it depends, and you should test it. Every game and every anti-cheat behaves differently, and there is no setting that is right for every game. Here is what I'd start with.

**CPU priority**

- My advice is to leave it at **Normal**. You'll see people say High priority makes games run worse because it starves Windows, but that isn't always true. Core Windows processes already run at elevated priority by default. You can check it yourself in Task Manager's Details tab: `dwm.exe`, `winlogon.exe`, `wininit.exe` and `ctfmon.exe` are good ones to look at.
- I haven't seen an FPS gain from setting a game to High either. If you try it, test it.
- **Avoid Realtime.** That one really can freeze your PC. ProcessX asks you to confirm before you use it.

**I/O priority**

- I'd keep it at **Normal** too. It only matters when the disk is busy, and games rarely need more than that. How it behaves can be different from PC to PC (SSD or HDD, downloads running in the background, and so on), so if you want to change it, **test it on your own setup**.

**GPU priority**

- This one can sometimes help. In my experience a higher GPU priority on the game can improve GPU load behavior and help with 1% lows. Again, **test it** because it won't matter for every game or every setup.

**CPU Sets**

- CPU Sets let you pick cores for games that don't normally allow changing affinity, Fortnite for example. It's not a promise for every game, but it's worth a try when the usual affinity way is blocked.
- The simple rule that works most of the time: **run the game on CCD0 only (AMD with two chiplets) or on the P-cores only (Intel hybrid)**. That keeps the game's threads off the slower or farther cores.
- Beyond that it's trial and error. Some games like **core 0 off**, some like **SMT/HT off**, and some get noticeably worse with either. Don't copy someone else's settings blindly. Try one change at a time and compare, using the same scene or benchmark each time.

**Anti-cheat**

- Some anti-cheat systems block or dislike changes to a protected game process, and that includes priority and CPU Set changes. If a change is refused, ProcessX tells you it was blocked by anti-cheat or a protected process.
- Some of them can also react badly to tools that touch their game. If you play something with a strict anti-cheat, be careful, and consider just not using a rule on that game.
- Valorant is the one to be most careful with. Changing its priorities can get your account suspended for a few days or weeks for using third-party software, so keep them at Normal and only change CPU Sets (they work fine).

## Building from source

You only need this if you want to change the code. Most people should use the release exe.

You need Python 3.8+, [psutil](https://pypi.org/project/psutil/), and optionally `pystray` and `Pillow` for the tray icon.

```bash
git clone https://github.com/jadenpeek/ProcessX.git
cd ProcessX
pip install psutil pystray pillow
python ProcessX.py
```

To build the exe yourself:

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --icon=processx.ico --name "ProcessX" --uac-admin ProcessX.py
```

## License

Free to use, modify and share, but not to sell, and the original author's name has to stay. See [LICENSE](LICENSE).