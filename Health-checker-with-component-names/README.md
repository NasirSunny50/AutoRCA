# Enterprise Health Dashboard - Simple Dynamic Version

## Requirements
- Python 3.12 or newer
- Windows, Linux, or macOS

## Run
1. Extract the ZIP.
2. Open Terminal/PowerShell in the extracted folder.
3. Create a virtual environment:
   `python -m venv .venv`
4. Activate it on PowerShell:
   `.venv\Scripts\Activate.ps1`
5. Install dependencies:
   `pip install -r requirements.txt`
6. Start:
   `python app.py`
7. Open:
   `http://127.0.0.1:5001`

## Dynamic configuration
- Projects and components are stored in `dashboard.db`.
- Add, edit, or delete them from the UI.
- No source-code change is required.
- Project-wise JSON import and export are supported.

## Project JSON format
```json
{
  "components": [
    {
      "name": "API Gateway",
      "health_url": "http://server:8080/actuator/health",
      "info_url": "http://server:8080/actuator/info",
      "metrics_url": "http://server:8080/actuator/metrics",
      "timeout": 5
    }
  ]
}
```

## Troubleshooting
- `requirements.txt not found`: open the terminal inside the extracted project folder.
- PowerShell activation blocked: run `Set-ExecutionPolicy -Scope Process Bypass` and activate again.
- Port 5001 is busy: close the other process using that port.
- Internal/private URLs must be reachable from the computer running this app.
