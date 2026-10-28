\# OpenLift — Setup Handoff



Follow these parts in order. Do not skip ahead. Each command block tells you:

\- \*\*Where:\*\* the folder your terminal must be in

\- \*\*Type:\*\* the exact command to copy and paste

\- \*\*Expect:\*\* what a successful result looks like



> \*\*Opening a terminal\*\*

> - \*\*macOS:\*\* press `Cmd + Space`, type `Terminal`, press Enter.

> - \*\*Windows:\*\* press the Windows key, type `PowerShell`, press Enter.

> - \*\*Linux:\*\* press `Ctrl + Alt + T`.

>

> To check which folder you are in, type `pwd` and press Enter. It prints the current folder path.



\---



\## Part A — Install the tools



\### A1. Install Git



\- \*\*macOS:\*\* In Terminal, type `git --version` and press Enter. If it is not installed, macOS will offer to install "Command Line Developer Tools". Click \*\*Install\*\* and wait.

\- \*\*Windows:\*\* Download from https://git-scm.com/download/win and run the installer. Click \*\*Next\*\* on every screen (the defaults are fine). Close and reopen PowerShell afterward.

\- \*\*Linux (Ubuntu/Debian):\*\* `sudo apt install git`



\*\*Where:\*\* any folder

\*\*Type:\*\*

```bash

git --version

```

\*\*Expect:\*\* something like `git version 2.47.0`. Any version 2.30+ is fine.



\### A2. Tell Git who you are (one-time)



\*\*Where:\*\* any folder

\*\*Type\*\* (replace with your name and the email on your GitHub account):

```bash

git config --global user.name "Your Name"

```

```bash

git config --global user.email "you@example.com"

```

\*\*Expect:\*\* no output. No output means it worked.



\### A3. Install the GitHub CLI and log in



This handles GitHub passwords for you so `git push` works.



\- \*\*macOS:\*\* install Homebrew first if you don't have it (https://brew.sh), then `brew install gh`

\- \*\*Windows:\*\* download the `.msi` from https://cli.github.com and run it. Reopen PowerShell afterward.

\- \*\*Linux:\*\* follow https://github.com/cli/cli/blob/trunk/docs/install\_linux.md



\*\*Where:\*\* any folder

\*\*Type:\*\*

```bash

gh auth login

```

Answer the prompts with the arrow keys and Enter:

1\. `GitHub.com`

2\. `HTTPS`

3\. `Authenticate Git with your GitHub credentials?` → `Yes`

4\. `Login with a web browser`

5\. Copy the 8-character code shown, press Enter, paste the code in the browser, click \*\*Authorize\*\*.



\*\*Expect:\*\* `✓ Logged in as <your-username>`



\### A4. Install Blender



Download \*\*Blender 4.2 LTS or newer\*\* from https://www.blender.org/download/ and install it normally.



You also need Blender reachable from the terminal (used from Phase 3 on):



\- \*\*macOS:\*\* Blender lives at `/Applications/Blender.app/Contents/MacOS/Blender`. Add a shortcut:

&#x20; ```bash

&#x20; echo 'alias blender="/Applications/Blender.app/Contents/MacOS/Blender"' >> \~/.zshrc

&#x20; ```

&#x20; Then close and reopen Terminal.

\- \*\*Windows:\*\* Press the Windows key, type `environment variables`, open \*\*Edit the system environment variables\*\* → \*\*Environment Variables...\*\* → under \*User variables\* select \*\*Path\*\* → \*\*Edit\*\* → \*\*New\*\* → paste the Blender folder, e.g. `C:\\Program Files\\Blender Foundation\\Blender 4.2` → \*\*OK\*\* on every window. Close and reopen PowerShell.

\- \*\*Linux:\*\* if installed via your package manager or Snap, `blender` is already on the path.



\*\*Where:\*\* any folder

\*\*Type:\*\*

```bash

blender --version

```

\*\*Expect:\*\* first line like `Blender 4.2.3 LTS`. If you see `command not found` / `not recognized`, redo the step above for your OS.



\---



\## Part B — Create the repo on GitHub and clone it



\### B1. Create the empty repo on GitHub



1\. Go to https://github.com/new

2\. \*\*Repository name:\*\* `openlift`

3\. \*\*Description:\*\* `Open-source 3D exercise models (GLB) for workout apps`

4\. Select \*\*Public\*\*.

5\. \*\*Leave all three "Initialize this repository with" options UNCHECKED\*\* (no README, no .gitignore, no license). We provide our own; checking them causes a conflict.

6\. Click \*\*Create repository\*\*.



\### B2. Pick a parent folder and clone



We will put the project in a `Projects` folder in your home directory.



\*\*Where:\*\* your home folder (a fresh terminal starts there)

\*\*Type:\*\*

```bash

mkdir -p \~/Projects

```

```bash

cd \~/Projects

```

\*\*Expect:\*\* no output. `pwd` now prints something like `/Users/you/Projects` (macOS), `C:\\Users\\you\\Projects` (Windows), or `/home/you/Projects` (Linux).



\*\*Where:\*\* `\~/Projects`

\*\*Type\*\* (replace `YOUR-USERNAME`):

```bash

git clone https://github.com/YOUR-USERNAME/openlift.git

```

\*\*Expect:\*\*

```

Cloning into 'openlift'...

warning: You appear to have cloned an empty repository.

```

The warning is correct and expected.



\*\*Where:\*\* `\~/Projects`

\*\*Type:\*\*

```bash

cd openlift

```

\*\*Expect:\*\* no output. `pwd` ends in `/Projects/openlift`. \*\*Every command from here on runs in this folder unless stated otherwise.\*\*



\### B3. Create the folder skeleton



Git does not track empty folders, so each gets a hidden empty `.gitkeep` file.



\*\*Where:\*\* `\~/Projects/openlift`



\*\*macOS / Linux — Type:\*\*

```bash

mkdir -p assets data docs exercises pipeline schema \&\& touch assets/.gitkeep data/.gitkeep docs/.gitkeep exercises/.gitkeep schema/.gitkeep

```



\*\*Windows PowerShell — Type:\*\*

```powershell

"assets","data","docs","exercises","pipeline","schema" | ForEach-Object { New-Item -ItemType Directory -Force -Path $\_ | Out-Null }; "assets","data","docs","exercises","schema" | ForEach-Object { New-Item -ItemType File -Force -Path "$\_\\.gitkeep" | Out-Null }

```



\*\*Expect:\*\* no output. Verify:

```bash

ls

```

\*\*Expect:\*\* `assets  data  docs  exercises  pipeline  schema`



\### B4. Create the Phase 1 files



Create these files inside `\~/Projects/openlift` and paste in the contents from the Phase 1 output:



| File | Location |

|---|---|

| `README.md` | `openlift/README.md` |

| `LICENSE` | `openlift/LICENSE` (no file extension) |

| `.gitignore` | `openlift/.gitignore` (starts with a dot, no extension) |

| `HANDOFF.md` | `openlift/HANDOFF.md` |

| `\_\_init\_\_.py` | `openlift/pipeline/\_\_init\_\_.py` |



Use a plain-text code editor. \*\*Do not use Word, Pages, or TextEdit in rich-text mode.\*\* Recommended: VS Code (https://code.visualstudio.com). In VS Code: \*\*File → Open Folder...\*\* → select `openlift`, then right-click in the sidebar → \*\*New File\*\*.



> \*\*Windows warning:\*\* Notepad may silently save `LICENSE` as `LICENSE.txt` and `.gitignore` as `.gitignore.txt`. VS Code avoids this.



\*\*Where:\*\* `\~/Projects/openlift`

\*\*Type:\*\*

```bash

git status

```

\*\*Expect:\*\* a red list under `Untracked files:` that includes `.gitignore`, `HANDOFF.md`, `LICENSE`, `README.md`, `assets/`, `docs/`, `exercises/`, `pipeline/`, `schema/`, `data/`.

If you see `LICENSE.txt` or `.gitignore.txt`, rename them to remove `.txt`.



\---



\## Part C — Base male mesh → `assets/base\_body.blend`



We use \*\*MPFB\*\* (the official MakeHuman plugin for Blender). Characters it generates from its built-in system assets are released under \*\*CC0\*\*, so the GLBs can be redistributed freely. It also creates a proper skeleton, which the pose pipeline requires. A bare FBX/OBJ from a random site often has no rig, or a license that blocks redistribution.



\### C1. Install MPFB



1\. Open Blender. Close the splash screen by clicking anywhere in the gray area.

2\. Top menu: \*\*Edit → Preferences...\*\*

3\. Left sidebar: \*\*Get Extensions\*\*.

4\. If you see a button \*\*Allow Online Access\*\*, click it.

5\. In the search box, type `MPFB`.

6\. Click \*\*Install\*\* next to \*\*MPFB\*\*.

7\. Close the Preferences window.



\*\*Expect:\*\* In the 3D viewport, press the `N` key. A sidebar opens on the right with vertical tabs. One tab is \*\*MPFB\*\*.



> If MPFB later says system assets are missing (no skins/clothes listed): download `makehuman\_system\_assets\_cc0.zip` from http://download.tuxfamily.org/makehuman/assets/ (or the link MPFB shows you), then in the MPFB tab open \*\*System and resources → Load pack from zip file\*\* and select it.



\### C2. Clear the default scene



1\. Hover your mouse over the 3D viewport.

2\. Press `A` (selects everything: cube, camera, light).

3\. Press `X`, then click \*\*Delete\*\*.



\*\*Expect:\*\* empty grid.



\### C3. Create the male body



In the \*\*MPFB\*\* tab (right sidebar, press `N` if hidden):



1\. Expand \*\*New human → From scratch\*\*.

2\. Set these sliders:

&#x20;  - \*\*Gender:\*\* `1.000` (fully male)

&#x20;  - \*\*Age:\*\* `0.500` (25 years)

&#x20;  - \*\*Muscle:\*\* `0.650`

&#x20;  - \*\*Weight:\*\* `0.500`

&#x20;  - \*\*Height:\*\* `0.500`

&#x20;  - \*\*Proportions:\*\* `0.500`

3\. \*\*Scale factor:\*\* set to \*\*Meter\*\*. (This is mandatory: 1 Blender unit must equal 1 meter.)

4\. Check \*\*Detailed helpers\*\* OFF and \*\*Extra vertex groups\*\* ON if those options are visible.

5\. Click \*\*Create human\*\*.



\*\*Expect:\*\* a gray human appears standing at the origin, roughly 1.75–1.80 units tall. Check: press `N` to see the \*\*Item\*\* tab, click the human, and the \*\*Dimensions → Z\*\* value should be about `1.75 m`. If it shows `17.5 m`, the scale factor was wrong: press `Ctrl+Z` and redo with \*\*Meter\*\*.



\### C4. Add the skeleton (rig)



With the human selected (click on it; it gets an orange outline):



1\. In the MPFB tab, expand \*\*Rigging → Add rig\*\*.

2\. Set \*\*Standard rig\*\* to \*\*Game engine\*\*.

3\. Click \*\*Add standard rig\*\*.



\*\*Expect:\*\* a stick skeleton appears inside the body. In the \*\*Outliner\*\* (top-right panel), there is now an object like `Human.rig` (an armature) with `Human` (the mesh) nested under it.



> We use the \*\*Game engine\*\* rig because it has clean, game-standard bone names (`upperarm\_l`, `thigh\_r`, `spine\_01`...). Phase 3's `bone\_mapping.json` will translate these to our standard names. If you choose a different rig, Phase 3 will still work, but tell me.



\### C5. Remove MakeHuman helper geometry



The MakeHuman mesh contains hidden "helper" geometry (for clothes fitting) that must not end up in the GLBs.



1\. In the MPFB tab with the human selected, look for \*\*Model → Remove helpers\*\* or \*\*New human → Delete helpers\*\* (wording varies by version) and click it.

2\. If you cannot find it, skip this. Phase 3's `validate\_rig.py` checks for helpers and the pipeline can strip them.



\### C6. Add black shorts



1\. Select the human mesh (not the skeleton). In the Outliner, click the object named `Human` (the mesh icon, not the bone icon).

2\. In the MPFB tab: \*\*Apply assets → Library → Clothes\*\*.

3\. Pick any short, snug male shorts/briefs item (e.g. `male\_casualsuit` bottoms, `shorts`, `swimming trunks`). The color does not matter; the pipeline recolors it black.

4\. Click \*\*Equip\*\*.



\*\*Expect:\*\* shorts appear on the body, and a new object appears in the Outliner under the rig.



5\. Rename that clothing object to exactly `shorts`: double-click its name in the Outliner, type `shorts`, press Enter.



> If no suitable shorts exist in your asset pack, skip this step. Phase 3 includes a fallback that builds procedural shorts from the body's hip/thigh vertices.



\### C7. Rename the main objects



In the Outliner, double-click to rename:



| Object type | New name |

|---|---|

| Armature (bone/stick icon) | `Armature` |

| Body mesh (triangle icon, the human) | `Body` |

| Shorts mesh | `shorts` |



\### C8. Save as `assets/base\_body.blend`



1\. Top menu: \*\*File → Save As...\*\*

2\. Navigate to `Projects/openlift/assets/`.

&#x20;  - macOS: `/Users/you/Projects/openlift/assets/`

&#x20;  - Windows: `C:\\Users\\you\\Projects\\openlift\\assets\\`

&#x20;  - Linux: `/home/you/Projects/openlift/assets/`

3\. File name: `base\_body.blend`

4\. Click \*\*Save As\*\*.

5\. Close Blender.



\*\*Where:\*\* `\~/Projects/openlift`

\*\*Type:\*\*

```bash

ls assets

```

\*\*Expect:\*\* `base\_body.blend` listed (`.gitkeep` is hidden). If you see `base\_body.blend1`, that's a backup copy and is ignored by Git.



\### C9. Check file size



GitHub rejects files over 100 MB.



\*\*Where:\*\* `\~/Projects/openlift`



macOS / Linux:

```bash

ls -lh assets/base\_body.blend

```

Windows PowerShell:

```powershell

(Get-Item assets\\base\_body.blend).Length / 1MB

```

\*\*Expect:\*\* under \~60 MB (typically 10–40 MB). If over 100 MB, stop and tell me; we will switch to Git LFS.



\---



\## Part D — Add your exercise CSV



1\. Find your file `exercise-database.csv`.

2\. Copy it into `Projects/openlift/data/`. The name must be exactly `exercise-database.csv` (lowercase, hyphens).

3\. Verify its header row is exactly:

&#x20;  ```

&#x20;  Muscle Group,Type,Exercise,Equipment,Also Works,Logged As,Exercise ID,Current Images

&#x20;  ```

&#x20;  If you exported from Excel/Numbers/Google Sheets, choose \*\*CSV UTF-8\*\* as the format.



\*\*Where:\*\* `\~/Projects/openlift`



macOS / Linux:

```bash

head -n 2 data/exercise-database.csv

```

Windows PowerShell:

```powershell

Get-Content data\\exercise-database.csv -TotalCount 2

```

\*\*Expect:\*\* the header line followed by your first exercise row.



\---



\## Part E — Commit and push



Run these one at a time.



\*\*Where:\*\* `\~/Projects/openlift`



```bash

git add .

```

\*\*Expect:\*\* no output. (On Windows you may see `warning: LF will be replaced by CRLF`. That is harmless.)



```bash

git status

```

\*\*Expect:\*\* green `new file:` lines for `.gitignore`, `HANDOFF.md`, `LICENSE`, `README.md`, `assets/.gitkeep`, `assets/base\_body.blend`, `data/.gitkeep`, `data/exercise-database.csv`, `docs/.gitkeep`, `exercises/.gitkeep`, `pipeline/\_\_init\_\_.py`, `schema/.gitkeep`. \*\*There must be no `.blend1` file.\*\*



```bash

git commit -m "Phase 1: repo init, handoff docs, base body, exercise CSV"

```

\*\*Expect:\*\* `\[main (root-commit) a1b2c3d] Phase 1: ...` followed by `12 files changed`.

(If it says `master` instead of `main`, that's fine; the next command fixes it.)



```bash

git branch -M main

```

\*\*Expect:\*\* no output.



```bash

git push -u origin main

```

\*\*Expect:\*\* progress lines, then:

```

To https://github.com/YOUR-USERNAME/openlift.git

&#x20;\* \[new branch]      main -> main

branch 'main' set up to track 'origin/main'.

```



Open `https://github.com/YOUR-USERNAME/openlift` in your browser. You should see the README rendered and the folders listed.



\---



\## Troubleshooting



| Symptom | Fix |

|---|---|

| `fatal: not a git repository` | You are in the wrong folder. Run `cd \~/Projects/openlift`. |

| `Permission denied` / `Authentication failed` on push | Run `gh auth login` again (Part A3). |

| `rejected ... (fetch first)` on push | You checked "Add a README" on GitHub. Run `git pull origin main --allow-unrelated-histories`, save the merge message (in the vim editor, type `:wq` and press Enter), then push again. |

| `File assets/base\_body.blend is 1xx MB; this exceeds GitHub's file size limit` | Stop and report; we'll set up Git LFS. |

| `blender: command not found` | Redo Part A4 for your OS and reopen the terminal. |

