# How to Start the AutoRCA Portal

**A step-by-step guide to getting the web portal up and running.**
Kona Software Lab LTD

---

## What you are starting

AutoRCA has **two** programs:

| Program | What it is | How to start it |
|---------|------------|-----------------|
| **Portal** | The website where you **view** the analysis results in your browser. | `python webapp.py` or `Start Portal.bat` |
| **Monitor** | The background worker that **watches the folder** and analyses new log files. | `python main.py` or `Start Monitor.bat` |

> They are independent. The **Portal** shows results; the **Monitor** produces
> them. You can run one or both. This guide is mainly about the **Portal**.

---

## Prerequisites (one time only)

1. **Python 3.10 or newer** must be installed.
   Check by opening a terminal and running:
   ```
   python --version
   ```

2. **Install the dependencies** (one time). In the `AutoRCA` folder, run:
   ```
   python -m pip install -r requirements.txt
   ```

3. **(Optional) Enable AI analysis.** For full Google Gemini analysis, make sure
   the file **`.env`** exists in the `AutoRCA` folder and contains your key:
   ```
   GEMINI_API_KEY=your_key_here
   ```
   > Without a key the system still works using the built-in offline engine — the
   > portal will still open and show results.

---

## The easy way — double-click

1. Open the **`AutoRCA`** folder in File Explorer.
2. **Double-click `Start Portal.bat`.**
3. A black window opens and your **browser launches automatically** at
   **http://localhost:5000**.
4. **Keep the black window open** while you use the portal.
5. To stop the portal, close that window (or press `Ctrl + C` inside it).

That's it.

---

## The manual way — using a terminal

1. Open **PowerShell** or **Command Prompt**.
2. Go to the project folder:
   ```
   cd F:\Personal_Passive_Income\AutoRCA
   ```
3. Start the portal:
   ```
   python webapp.py
   ```
4. You will see:
   ```
   ====================================================
     AutoRCA portal is running.
     Open this in your browser:  http://localhost:5000
     Keep this window open. Press Ctrl+C to stop.
   ====================================================
   ```
5. Your browser opens automatically. If not, open **http://localhost:5000**
   yourself.
6. **Leave the terminal open.** Closing it or pressing `Ctrl + C` stops the portal.

---

## Starting the Monitor too (to analyse new logs)

If you also want new log files to be analysed automatically, start the Monitor in
a **second** window:

- Double-click **`Start Monitor.bat`**, **or**
- In a new terminal: `python main.py`

Then drop any `.log`, `.txt`, `.out`, or `.trace` file into the
**`Error Log File`** folder. Within a few seconds it is analysed and appears in
the portal (the dashboard refreshes on its own).

---

## Verifying it is working

- The browser shows the **AutoRCA Dashboard** with stat cards and recent analyses.
- The terminal/black window shows `Running on http://127.0.0.1:5000`.
- Click any report to open the detailed root-cause view.

---

## Troubleshooting

**The page won't load / "This site can't be reached"**
- The portal program must be **running** — check that the black window/terminal
  is still open. If you closed it, start it again.
- Try **http://127.0.0.1:5000** instead of `http://localhost:5000`.

**"Port 5000 is busy"**
- This is handled automatically — the portal will use the next free port (e.g.
  5001) and print the new address. Open the address it prints.
- To free port 5000 manually (Windows), find and stop the process:
  ```
  netstat -ano | findstr :5000
  taskkill /PID <the_PID_shown> /F
  ```

**A black window flashes and closes immediately**
- There is a startup error. Start it from a terminal instead
  (`python webapp.py`) so the error stays on screen, and read the message.
- The most common cause is missing dependencies — run
  `python -m pip install -r requirements.txt` again.

**"python is not recognized"**
- Python is not installed or not on your PATH. Install Python 3.10+ and tick
  *"Add Python to PATH"* during installation.

**The page loads but looks broken / blank**
- Do a hard refresh: **Ctrl + F5**.

**It says analyses used the offline engine, not Gemini**
- The `.env` file is missing or the `GEMINI_API_KEY` is empty/invalid, or the
  free quota was momentarily exceeded. Add or correct the key in `.env`; the
  portal works either way.

---

## Quick reference

| I want to… | Do this |
|------------|---------|
| Open the portal | Double-click `Start Portal.bat` → browser opens at http://localhost:5000 |
| Analyse new logs | Double-click `Start Monitor.bat`, then drop files into `Error Log File` |
| Stop the portal | Close its window, or press `Ctrl + C` in it |
| Use a different port | `python webapp.py --port 8080` |
| Open on another device on the network | `python webapp.py --host 0.0.0.0`, then visit `http://<this-PC-IP>:5000` |

---

*AutoRCA — Automated Log Monitoring & Error Analysis · Kona Software Lab LTD*
