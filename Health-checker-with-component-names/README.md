# Dynamic CMS Health Checker

## Run
```bash
pip install -r requirements.txt
python app.py
```
Open: http://127.0.0.1:5001

## New restart-time feature
The dashboard now checks restart/startup time in two ways:

1. From `/health` response fields such as `startTime`, `startedAt`, `startupTime`, `upSince`, `uptime`, or nested Spring Boot style fields.
2. If `/health` does not contain restart info, it automatically calls `/info` or `/actuator/info` for the same service and displays the restart time when available.

It also shows restart source, build date, and git hash when `/info` provides them.

## Input Options
1. Manual base URL/IP + health path + ports
2. Upload Postman collection JSON
3. Upload component list JSON
