from flask import Flask, render_template, request, jsonify
import requests, time, json
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024


def extract_services_from_postman(collection):
    services = []
    def walk(items, folder=''):
        for item in items or []:
            name = item.get('name', '')
            if 'item' in item:
                walk(item.get('item'), name or folder)
            elif 'request' in item:
                req = item.get('request', {})
                method = req.get('method', 'GET')
                if method.upper() != 'GET':
                    continue
                url_obj = req.get('url', {})
                raw = url_obj.get('raw') if isinstance(url_obj, dict) else str(url_obj)
                if not raw:
                    continue
                if '/health' in raw:
                    p = urlparse(raw)
                    services.append({
                        'componentName': folder or name or p.netloc,
                        'requestName': name,
                        'port': int(p.port or (443 if p.scheme == 'https' else 80)),
                        'url': raw
                    })
    walk(collection.get('item', []))
    return services


def normalize_service(payload):
    component = payload.get('componentName') or payload.get('name') or payload.get('component') or 'Service'
    url = payload.get('url')
    if not url:
        base = (payload.get('baseUrl') or '').rstrip('/')
        port = str(payload.get('port') or '').strip().replace(':', '')
        path = payload.get('healthPath') or '/health'
        if not path.startswith('/'):
            path = '/' + path
        url = f'{base}:{port}{path}'
    return component, url


def parse_datetime_value(value):
    if value in (None, '', '-', 'null'):
        return None
    if isinstance(value, (int, float)):
        # Epoch milliseconds or seconds
        if value > 10_000_000_000:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        if value > 1_000_000_000:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return text


def restart_from_uptime(value):
    try:
        if isinstance(value, str):
            # Supports plain seconds: "86400" or "86400.5"
            value = float(value.strip())
        value = float(value)
        # if milliseconds looks large, convert to seconds
        seconds = value / 1000 if value > 10_000_000 else value
        dt = datetime.now() - timedelta(seconds=seconds)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return None


def parse_restart_time(data):
    """Parse restart/startup time from health/info JSON.
    Supports custom fields, common Spring Boot fields, uptime seconds, and nested details.
    """
    if not isinstance(data, dict):
        return None

    direct_keys = [
        'startTime', 'start_time', 'startedAt', 'started_at', 'startupTime', 'startup_time',
        'upSince', 'up_since', 'launchTime', 'launch_time', 'bootTime', 'boot_time',
        'instanceStartTime', 'applicationStartedAt', 'applicationStartTime'
    ]
    for k in direct_keys:
        if data.get(k):
            return parse_datetime_value(data.get(k))

    # Uptime fields: calculate last restart time
    uptime_keys = ['uptime', 'upTime', 'uptimeSeconds', 'uptime_seconds', 'processUptime', 'process_uptime']
    for k in uptime_keys:
        if data.get(k) is not None:
            val = restart_from_uptime(data.get(k))
            if val:
                return val

    # Spring Boot style: {"process":{"uptime":123}} or {"application":{"startedAt":"..."}}
    for parent in ['process', 'application', 'app', 'runtime', 'system']:
        obj = data.get(parent)
        if isinstance(obj, dict):
            for k in direct_keys:
                if obj.get(k):
                    return parse_datetime_value(obj.get(k))
            for k in uptime_keys:
                if obj.get(k) is not None:
                    val = restart_from_uptime(obj.get(k))
                    if val:
                        return val

    # Actuator /health details tree
    details = data.get('details') or data.get('components') or {}
    if isinstance(details, dict):
        for v in details.values():
            if isinstance(v, dict):
                # component may hold details under "details"
                nested = v.get('details') if isinstance(v.get('details'), dict) else v
                found = parse_restart_time(nested)
                if found:
                    return found
    return None


def component_status(data, key):
    try:
        val = data.get(key, {}) if isinstance(data, dict) else {}
        if isinstance(val, dict):
            return val.get('status') or val.get('state') or '-'
        return str(val)
    except Exception:
        return '-'


def make_info_urls(health_url):
    p = urlparse(health_url)
    path = p.path or ''
    candidates = []
    if path.endswith('/health'):
        candidates.append(path[:-len('/health')] + '/info')
        candidates.append(path[:-len('/health')] + '/actuator/info')
    if '/actuator/health' in path:
        candidates.insert(0, path.replace('/actuator/health', '/actuator/info'))
    candidates.extend(['/info', '/actuator/info'])
    urls = []
    seen = set()
    for cp in candidates:
        cp = cp or '/info'
        u = urlunparse((p.scheme, p.netloc, cp, '', '', ''))
        if u not in seen:
            urls.append(u); seen.add(u)
    return urls


def fetch_info_restart(health_url, timeout_seconds):
    """Option 3: automatically call /info or /actuator/info to get startup/restart info."""
    errors = []
    for info_url in make_info_urls(health_url):
        try:
            r = requests.get(info_url, timeout=timeout_seconds, headers={'Accept': 'application/json,text/plain,*/*'})
            if not r.ok:
                errors.append(f'{info_url} HTTP {r.status_code}')
                continue
            try:
                data = r.json()
            except Exception:
                errors.append(f'{info_url} non-json')
                continue
            restart_at = parse_restart_time(data)
            build_date = data.get('buildDate') or (data.get('build') or {}).get('time') if isinstance(data, dict) else None
            git_hash = None
            if isinstance(data, dict) and isinstance(data.get('git'), dict):
                git_hash = data['git'].get('hash') or data['git'].get('commit', {}).get('id')
            return restart_at, info_url, parse_datetime_value(build_date), git_hash, None
        except Exception as e:
            errors.append(f'{info_url} {str(e)}')
    return None, None, None, None, '; '.join(errors[:2]) if errors else None


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/parse-file', methods=['POST'])
def parse_file():
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'error': 'No file uploaded'}), 400
    try:
        data = json.loads(f.read().decode('utf-8'))
        services = extract_services_from_postman(data)
        if not services:
            arr = data.get('services') or data.get('components') or data.get('endpoints') or []
            base = data.get('baseUrl') or ''
            path = data.get('healthPath') or '/health'
            services = []
            for i, s in enumerate(arr):
                comp, url = normalize_service({**s, 'baseUrl': base, 'healthPath': path})
                services.append({'componentName': comp, 'port': s.get('port'), 'url': url})
        return jsonify({'ok': True, 'services': services})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 400


@app.route('/api/check', methods=['POST'])
def api_check():
    body = request.get_json(force=True)
    component, url = normalize_service(body)
    timeout_ms = int(body.get('timeout', 5000))
    timeout_seconds = timeout_ms / 1000
    started = time.time()
    try:
        r = requests.get(url, timeout=timeout_seconds, headers={'Accept': 'application/json,text/plain,*/*'})
        elapsed = int((time.time() - started) * 1000)
        text = r.text[:2000]
        try:
            data = r.json()
        except Exception:
            data = {}
        status_text = str(data.get('status') or data.get('health') or data.get('state') or '').upper() if isinstance(data, dict) else ''
        up_words = {'UP','OK','HEALTHY','RUNNING','ACTIVE','ONLINE'}
        is_up = r.ok and (not status_text or status_text in up_words)

        # Option 2: check startup fields from /health response.
        restart_at = parse_restart_time(data)
        restart_source = 'health' if restart_at else None
        build_date = None
        git_hash = None
        info_error = None

        # Option 3: if health response does not include restart time, call /info automatically.
        if not restart_at:
            restart_at, info_url, build_date, git_hash, info_error = fetch_info_restart(url, timeout_seconds)
            if restart_at:
                restart_source = info_url

        return jsonify({
            'componentName': component,
            'url': url,
            'state': 'up' if is_up else 'down',
            'httpStatus': r.status_code,
            'responseTimeMs': elapsed,
            'application': component_status(data, 'application'),
            'db': component_status(data, 'db'),
            'redis': component_status(data, 'redis'),
            'rabbit': component_status(data, 'rabbit'),
            'restartAt': restart_at,
            'restartSource': restart_source,
            'buildDate': build_date,
            'gitHash': git_hash,
            'infoError': info_error,
            'checkedAt': datetime.now().strftime('%H:%M:%S'),
            'message': text if not is_up else 'OK'
        })
    except Exception as e:
        return jsonify({
            'componentName': component,
            'url': url,
            'state': 'down',
            'httpStatus': None,
            'responseTimeMs': None,
            'application': '-', 'db': '-', 'redis': '-', 'rabbit': '-',
            'restartAt': None,
            'restartSource': None,
            'buildDate': None,
            'gitHash': None,
            'infoError': None,
            'checkedAt': datetime.now().strftime('%H:%M:%S'),
            'message': str(e)
        })


if __name__ == '__main__':
    # Port 5001 so it can run alongside the AutoRCA portal (which uses 5000).
    app.run(debug=True, host='127.0.0.1', port=5001)
