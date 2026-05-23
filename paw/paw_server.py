import os
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
import argparse
import requests
import sys
import subprocess
import json
import threading
import time
import signal
import uuid
import asyncio
from datetime import datetime
import logging

try:
    from flask_sock import Sock
except ImportError:
    Sock = None

try:
    from simple_websocket import Server as SimpleWebSocketServer
    from simple_websocket.errors import ConnectionClosed
except ImportError:
    SimpleWebSocketServer = None
    ConnectionClosed = None

try:
    from asgiref.wsgi import WsgiToAsgi
except ImportError:
    WsgiToAsgi = None


class Paw:
    def __init__(self, filename, verbose=False):
        self.__dbname = filename
        if filename != ':memory:':
            os.path.abspath(filename)
        self.__conn = None
        self.cursor = None
        self.__verbose = verbose
        self.__lock = threading.Lock()
        self.__open()

    def __open(self):
        """Open database connection with retry mechanism"""
        max_retries = 5
        for attempt in range(max_retries):
            try:
                self.__conn = sqlite3.connect(self.__dbname, check_same_thread=False, timeout=30.0)
                self.__conn.isolation_level = None
                self.cursor = self.__conn.cursor()
                fields = ('word', 'exp')
                self.__fields = tuple([(fields[i], i) for i in range(len(fields))])
                self.__names = {}
                for k, v in self.__fields:
                    self.__names[k] = v
                return True
            except sqlite3.Error as e:
                logging.warning(f"Database connection attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(1)

    def __ensure_connection(self):
        """Ensure database connection is alive"""
        if self.__conn is None:
            self.__open()
        else:
            try:
                # Test connection
                self.cursor.execute("SELECT 1")
            except sqlite3.Error:
                logging.warning("Database connection lost, reconnecting...")
                self.__open()
    def candidates(self):
        with self.__lock:
            self.__ensure_connection()
            try:
                with self.__conn:
                    # Define the SQL query to join the items and status tables
                    self.cursor.execute("""
                        SELECT items.word, items.exp, status.origin_path, status.note
                        FROM items
                        JOIN status ON items.word = status.word
                    """)
                    items = self.cursor.fetchall()
                    if not items:
                        return {}  # Return empty dict instead of raising exception

                    # Process the fetched items into the desired dictionary format
                    words = {
                        item[0].strip('"'): {
                            "word": item[0].strip('\"'),
                            "exp": "" if item[1] is None else item[1].strip('\"'),
                            "origin_path": "" if item[2] is None else os.path.basename(item[2].strip('\"')),  # origin_path from status table
                            "note": "" if item[3] is None else item[3].strip('\"')      # note from status table
                        }
                        for item in items
                    }
                    return words
            except Exception as e:
                logging.error(f"Error fetching candidates: {e}")
                return {}
    def delete(self, word):
        with self.__lock:
            self.__ensure_connection()
            try:
                word = '"' + word + '"'
                with self.__conn:
                    self.cursor.execute("DELETE FROM items WHERE word=?", (word,))
                    if self.cursor.rowcount == 0:
                        raise Exception("Word not found")
                    self.__conn.commit()
                    # Add additional deletion from 'status' table if necessary
                    self.cursor.execute("DELETE FROM status WHERE word=?", (word,))
                    self.__conn.commit()
                return True
            except Exception as e:
                logging.error(f"Error deleting word {word}: {e}")
                raise

app = Flask(__name__)
CORS(app)
sock = Sock(app) if Sock else None
websocket_available = bool(sock or SimpleWebSocketServer or WsgiToAsgi)
# Example usage of the parsed arguments
database = None
save_dir = None
port = None
wallabag_host = None
wallabag_username = None
wallabag_password = None
wallabag_clientid = None
wallabag_secret = None
wallabag_token = None  # This will be set after requesting a token
paw = None

media_lock = threading.Lock()
media_pending = {}
media_ws = None
media_last_status = None
media_last_status_by_key = {}

SECRET_TOKEN = "your-secure-token"

def media_empty_status(last_error="no-media-cache"):
    return {
        "ok": True,
        "source": "empty",
        "stale": True,
        "provider": None,
        "mediaId": None,
        "url": None,
        "title": None,
        "currentTimeMs": 0,
        "durationMs": 0,
        "remainingMs": 0,
        "paused": True,
        "playbackRate": 1.0,
        "canControl": False,
        "updatedAtMs": 0,
        "lastError": last_error,
    }

def normalize_media_status(data, source="fresh", stale=False, last_error=None):
    now = int(time.time() * 1000)
    current = int(data.get("currentTimeMs") or 0)
    duration = int(data.get("durationMs") or 0)
    remaining = data.get("remainingMs")
    if remaining is None:
        remaining = max(0, duration - current)

    return {
        "ok": True,
        "source": source,
        "stale": stale,
        "provider": data.get("provider"),
        "mediaId": data.get("mediaId"),
        "url": data.get("url"),
        "title": data.get("title"),
        "currentTimeMs": current,
        "durationMs": duration,
        "remainingMs": int(remaining or 0),
        "paused": bool(data.get("paused", True)),
        "playbackRate": float(data.get("playbackRate") or 1.0),
        "canControl": bool(data.get("canControl", False)) and not stale,
        "updatedAtMs": int(data.get("updatedAtMs") or now),
        "lastError": last_error,
    }

def media_cache_key(target_url=None):
    return target_url or "__default__"

def media_cached_status(last_error, target_url=None):
    global media_last_status
    key = media_cache_key(target_url)
    with media_lock:
        keyed = media_last_status_by_key.get(key)
        if target_url:
            cached = dict(keyed) if keyed else None
        else:
            cached = dict(keyed or media_last_status) if (keyed or media_last_status) else None

    if not cached:
        return media_empty_status(last_error)

    cached["source"] = "cache"
    cached["stale"] = True
    cached["paused"] = True
    cached["canControl"] = False
    cached["lastError"] = last_error
    return cached

def store_media_status(status, target_url=None):
    global media_last_status
    key = media_cache_key(target_url)
    with media_lock:
        media_last_status = dict(status)
        media_last_status_by_key[key] = dict(status)
    logging.debug(
        "media cache updated key=%s provider=%s title=%r current=%sms duration=%sms paused=%s",
        key,
        status.get("provider"),
        status.get("title"),
        status.get("currentTimeMs"),
        status.get("durationMs"),
        status.get("paused"),
    )

def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def get_media_ws():
    with media_lock:
        return media_ws

def fulfill_media_request(message):
    request_id = message.get("requestId")
    if not request_id:
        logging.debug("media response ignored: missing requestId")
        return

    with media_lock:
        pending = media_pending.get(request_id)

    if pending:
        pending["response"] = message
        pending["event"].set()
        logging.debug(
            "media response received requestId=%s ok=%s canControl=%s provider=%s error=%s",
            request_id,
            message.get("ok"),
            message.get("canControl"),
            message.get("provider"),
            message.get("error"),
        )
    else:
        logging.debug("media response ignored: no pending requestId=%s", request_id)

class AsgiMediaWebSocket:
    def __init__(self, send, loop):
        self.send_asgi = send
        self.loop = loop

    def send(self, data):
        future = asyncio.run_coroutine_threadsafe(
            self.send_asgi({"type": "websocket.send", "text": data}),
            self.loop,
        )
        future.result(timeout=5)

def media_ws_loop(ws):
    global media_ws
    with media_lock:
        media_ws = ws
    logging.info("media websocket connected")

    try:
        while True:
            try:
                raw = ws.receive()
            except Exception as e:
                if ConnectionClosed and isinstance(e, ConnectionClosed):
                    logging.info("media websocket closed: code=%s reason=%s", e.reason, e.message)
                    break
                raise
            if raw is None:
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logging.warning("media websocket received invalid JSON")
                continue
            if message.get("type") == "media.response":
                fulfill_media_request(message)
            else:
                logging.debug("media websocket message type=%s", message.get("type"))
    finally:
        with media_lock:
            if media_ws is ws:
                media_ws = None
        logging.info("media websocket disconnected")

@app.route("/media/request", methods=["POST"])
def media_request_endpoint():
    if not websocket_available:
        logging.warning("media request failed: websocket dependency missing")
        return jsonify(media_cached_status("websocket-dependency-missing"))

    data = request.json or {}
    request_id = data.get("requestId") or str(uuid.uuid4())
    timeout_ms = min(int(data.get("timeoutMs") or 800), 1200)
    event = threading.Event()

    message = {
        "type": "media.request",
        "requestId": request_id,
        "target": data.get("target") or "active-or-last",
        "action": data.get("action") or "status",
        "deltaMs": data.get("deltaMs"),
        "positionMs": data.get("positionMs"),
        "targetUrl": data.get("targetUrl") or data.get("url"),
        "mediaId": data.get("mediaId"),
        "timeoutMs": timeout_ms,
    }
    target_url = message.get("targetUrl")

    ws = get_media_ws()
    if ws is None:
        logging.debug(
            "media request using cache: extension unavailable action=%s requestId=%s",
            message["action"],
            request_id,
        )
        return jsonify(media_cached_status("extension-unavailable", target_url))

    with media_lock:
        media_pending[request_id] = {"event": event, "response": None}

    try:
        logging.debug(
            "media request send action=%s target=%s requestId=%s timeoutMs=%s",
            message["action"],
            message.get("targetUrl") or message["target"],
            request_id,
            timeout_ms,
        )
        ws.send(json.dumps(message))
    except Exception as e:
        with media_lock:
            media_pending.pop(request_id, None)
        logging.warning("media request send failed requestId=%s error=%s", request_id, e)
        return jsonify(media_cached_status(f"extension-send-failed:{e}", target_url))

    if not event.wait(timeout_ms / 1000.0):
        with media_lock:
            media_pending.pop(request_id, None)
        logging.debug("media request timeout action=%s requestId=%s", message["action"], request_id)
        return jsonify(media_cached_status("extension-timeout", target_url))

    with media_lock:
        pending = media_pending.pop(request_id, None)

    response = pending.get("response") if pending else None
    if not response or not response.get("ok") or not response.get("canControl"):
        logging.debug(
            "media request using cache: no controllable media action=%s requestId=%s response=%s",
            message["action"],
            request_id,
            response,
        )
        return jsonify(media_cached_status((response or {}).get("error") or "no-media", target_url))

    status = normalize_media_status(response)
    store_media_status(status, target_url)
    logging.debug(
        "media request fresh action=%s requestId=%s provider=%s targetUrl=%s url=%s",
        message["action"],
        request_id,
        status.get("provider"),
        message.get("targetUrl"),
        status.get("url"),
    )
    return jsonify(status)

if sock:
    @sock.route("/media/ws")
    def media_ws_endpoint(ws):
        media_ws_loop(ws)
elif SimpleWebSocketServer:
    @app.route("/media/ws", websocket=True)
    def media_ws_endpoint():
        ws = SimpleWebSocketServer.accept(request.environ)
        media_ws_loop(ws)

async def asgi_media_ws_endpoint(scope, receive, send):
    global media_ws
    message = await receive()
    if message.get("type") != "websocket.connect":
        return

    await send({"type": "websocket.accept"})
    ws = AsgiMediaWebSocket(send, asyncio.get_running_loop())
    with media_lock:
        media_ws = ws
    logging.info("media websocket connected")

    try:
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                logging.info("media websocket closed: code=%s", message.get("code"))
                break
            if message_type != "websocket.receive":
                continue

            raw = message.get("text")
            if raw is None and message.get("bytes") is not None:
                raw = message["bytes"].decode("utf-8")
            if raw is None:
                continue

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                logging.warning("media websocket received invalid JSON")
                continue
            if payload.get("type") == "media.response":
                fulfill_media_request(payload)
            else:
                logging.debug("media websocket message type=%s", payload.get("type"))
    finally:
        with media_lock:
            if media_ws is ws:
                media_ws = None
        logging.info("media websocket disconnected")

def create_asgi_app():
    if WsgiToAsgi is None:
        raise RuntimeError("asgiref is required for ASGI production mode")

    flask_asgi_app = WsgiToAsgi(app)

    async def asgi_app(scope, receive, send):
        if scope["type"] == "websocket" and scope.get("path") == "/media/ws":
            await asgi_media_ws_endpoint(scope, receive, send)
            return
        await flask_asgi_app(scope, receive, send)

    return asgi_app

# -------------------------------
# Call paw-org-protocol in Emacs
# -------------------------------
def call_paw_org_protocol(data: dict):
    """
    Calls the existing Emacs function (paw-org-protocol data)
    using emacsclient -e safely.
    """
    # Convert Python dict to JSON string, escape double quotes
    # JSON to string, escape double quotes and newlines
    json_str = json.dumps(data, ensure_ascii=False)
    json_str = json_str.replace('\\', '\\\\').replace('"', '\\"')
    # print(json_str)

    elisp_code = f'(paw-org-protocol (json-parse-string "{json_str}" :object-type \'plist :array-type \'list))'

    try:
        result = subprocess.run(
            ["emacsclient", "-e", elisp_code],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"

# -------------------------------
# Flask endpoint /paw
# -------------------------------
@app.route("/paw", methods=["POST"])
def paw_endpoint():
    token = request.headers.get("X-Auth-Token")
    if token != SECRET_TOKEN:
        return jsonify({"status": "error", "error": "Unauthorized"}), 401

    data = request.json
    if not data:
        return jsonify({"status": "error", "error": "No data provided"}), 400

    result = call_paw_org_protocol(data)
    return jsonify({"status": "ok", "result": result})

@app.route('/words', methods=['GET'])
def get_words():
    try:
        words = { "wordInfos":  paw.candidates() }
        # print(words)
        return jsonify(words)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/words', methods=['DELETE'])
def delete_word():
    data = request.json
    word = data.get('word')
    if not word:
        return jsonify({"error": "No word provided"}), 400

    try:
        paw.delete(word)
        return jsonify({"status": "success", "word": word}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/source', methods=['POST'])
def receive_source():
    global save_dir
    source_code = request.json.get('source')
    if source_code:
        try:
            # Determine if save_dir is a file or directory
            if os.path.splitext(save_dir)[1]:  # save_dir has an extension, treat it as a file
                # Ensure the directory for the file exists
                os.makedirs(os.path.dirname(save_dir), exist_ok=True)
                temp_file_path = save_dir
            else:
                # Ensure the directory exists
                os.makedirs(save_dir, exist_ok=True)
                # Use a default filename within the directory
                temp_file_path = os.path.join(save_dir, "source.html")

            with open(temp_file_path, 'w') as temp_file:
                temp_file.write(source_code)
            print(f"Received source code saved to {temp_file_path}")
            return jsonify({"status": "success", "temp_file_path": temp_file_path}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "No source code provided"}), 400


def request_token(wallabag_host, wallabag_username, wallabag_password, wallabag_clientid, wallabag_secret):
    try:
        response = requests.post(
            f"{wallabag_host}/oauth/v2/token",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.101 Safari/537.36",
                "Content-Type": "application/json"
            },
            json={
                "username": wallabag_username,
                "password": wallabag_password,
                "client_id": wallabag_clientid,
                "client_secret": wallabag_secret,
                "grant_type": "password"
            }
        )
        response.raise_for_status()
        return response.json().get('access_token')
    except requests.RequestException as e:
        print(f"Error requesting token: {e}")
        return None

@app.route('/wallabag/entry', methods=['POST'])
def wallabag_insert_entry():
    global wallabag_token
    data = request.json
    url = data.get("url")
    title = data.get("title")
    content = data.get("content")
    if not url:
        return jsonify({"error": "URL is required"}), 400
    def insert_entry_with_token(token):
        try:
            response = requests.post(
                f"{wallabag_host}/api/entries.json",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2272.101 Safari/537.36",
                    "Authorization": f"Bearer {token}"
                },
                json={
                    "url": url,
                    "title": title,
                    "content": content,
                    "archive": 0,
                    "starred": 0,
                    "tags": ""  # If you have tags, you can set them here or modify this accordingly
                }
            )
            if response.status_code == 401:
                return None  # Token might be expired
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error inserting entry: {e}")
            return None
    # Request a new token if it's not already set
    if wallabag_token is None:
        wallabag_token = request_token(wallabag_host, wallabag_username, wallabag_password,
                                       wallabag_clientid, wallabag_secret)
    if wallabag_token is None:
        return jsonify({"error": "Failed to obtain access token"}), 500
    result = insert_entry_with_token(wallabag_token)
    if result is None:
        # Token might be expired, get a new one and retry
        wallabag_token = request_token(wallabag_host, wallabag_username, wallabag_password,
                                       wallabag_clientid, wallabag_secret)
        if wallabag_token is None:
            return jsonify({"error": "Failed to obtain access token"}), 500
        result = insert_entry_with_token(wallabag_token)
        if result is None:
            return jsonify({"error": "Failed to insert entry after refreshing token"}), 500
    return jsonify({"status": "success", "data": result}), 200

def run_server(database_path, temp_dir, port, host, username, password, clientid, secret):
    global wallabag_host, wallabag_username, wallabag_password, wallabag_clientid, wallabag_secret, paw, save_dir

    # Setup logging
    log_level = getattr(logging, os.getenv('PAW_LOG_LEVEL', 'INFO').upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler('paw-server.log')
        ]
    )

    # Use environment variables as fallback
    wallabag_host = host or os.getenv('WALLABAG_HOST')
    wallabag_username = username or os.getenv('WALLABAG_USERNAME')
    wallabag_password = password or os.getenv('WALLABAG_PASSWORD')
    wallabag_clientid = clientid or os.getenv('WALLABAG_CLIENTID')
    wallabag_secret = secret or os.getenv('WALLABAG_SECRET')

    if database_path:
        try:
            # Expand user home directory (~) if present
            expanded_db_path = os.path.expanduser(database_path)
            paw = Paw(expanded_db_path)
            logging.info(f"Connected to database: {expanded_db_path}")
        except Exception as e:
            logging.error(f"Failed to connect to database {database_path}: {e}")
            sys.exit(1)

    save_dir = temp_dir or os.getenv('PAW_SAVE_DIR', '/tmp')
    port = int(port or os.getenv('PAW_PORT', 5001))

    logging.info(f"Starting PAW server on port {port}")
    logging.info(f"Log level: {logging.getLevelName(log_level)}")
    logging.info(f"Media websocket available: {websocket_available}")
    logging.info(f"Save directory: {save_dir}")
    if wallabag_host:
        logging.info(f"Wallabag integration enabled for: {wallabag_host}")

    # Setup graceful shutdown
    def signal_handler(sig, frame):
        logging.info("Received shutdown signal, closing database connections...")
        if paw and paw._Paw__conn:
            paw._Paw__conn.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Use environment variable to decide server type
    server_type = os.getenv('PAW_SERVER_TYPE', 'flask')
    logging.info(f"Server type detected: {server_type}")

    if server_type == 'production':
        try:
            import uvicorn
            access_log = env_bool('PAW_ACCESS_LOG', False)
            logging.info("Starting server with uvicorn (production ASGI server)...")
            logging.info(f"Uvicorn access log: {access_log}")
            uvicorn.run(
                create_asgi_app(),
                host='0.0.0.0',
                port=port,
                log_level=logging.getLevelName(log_level).lower(),
                access_log=access_log,
            )
        except ImportError as e:
            logging.error(f"uvicorn/asgiref not available ({e}); install production dependencies")
            sys.exit(1)
        except Exception as e:
            logging.error(f"Error starting uvicorn server ({e})")
            sys.exit(1)
    elif server_type == 'waitress':
        try:
            from waitress import serve
            logging.info("Starting server with waitress (HTTP-only WSGI server)...")
            serve(app, host='0.0.0.0', port=port)
        except ImportError as e:
            logging.warning(f"waitress not available ({e}), falling back to Flask development server")
            app.run(host='0.0.0.0', port=port, threaded=True)
        except Exception as e:
            logging.error(f"Error starting waitress server ({e}), falling back to Flask development server")
            app.run(host='0.0.0.0', port=port, threaded=True)
    else:
        logging.info("Starting server with Flask development server...")
        app.run(host='0.0.0.0', port=port, threaded=True)


def main():
    """Standalone server entry point"""
    parser = argparse.ArgumentParser(description='PAW Server - Standalone Mode')
    parser.add_argument('--database', type=str,
                       default=os.getenv('PAW_DATABASE_PATH'),
                       help='Path to SQLite database file (env: PAW_DATABASE_PATH)')
    parser.add_argument('--save-dir', type=str,
                       default=os.getenv('PAW_SAVE_DIR', '/tmp'),
                       help='Directory to save files (env: PAW_SAVE_DIR)')
    parser.add_argument('--port', type=int,
                       default=int(os.getenv('PAW_PORT', 5001)),
                       help='Server port (env: PAW_PORT)')
    parser.add_argument('--wallabag-host', type=str,
                       default=os.getenv('WALLABAG_HOST'),
                       help='Wallabag host URL (env: WALLABAG_HOST)')
    parser.add_argument('--wallabag-username', type=str,
                       default=os.getenv('WALLABAG_USERNAME'),
                       help='Wallabag username (env: WALLABAG_USERNAME)')
    parser.add_argument('--wallabag-password', type=str,
                       default=os.getenv('WALLABAG_PASSWORD'),
                       help='Wallabag password (env: WALLABAG_PASSWORD)')
    parser.add_argument('--wallabag-clientid', type=str,
                       default=os.getenv('WALLABAG_CLIENTID'),
                       help='Wallabag client ID (env: WALLABAG_CLIENTID)')
    parser.add_argument('--wallabag-secret', type=str,
                       default=os.getenv('WALLABAG_SECRET'),
                       help='Wallabag client secret (env: WALLABAG_SECRET)')
    parser.add_argument('--server-type', type=str,
                       choices=['flask', 'production', 'waitress'],
                       default=os.getenv('PAW_SERVER_TYPE', 'flask'),
                       help='Server type to use: flask, production (uvicorn ASGI), or waitress (HTTP-only WSGI) (env: PAW_SERVER_TYPE)')

    args = parser.parse_args()

    # Set server type environment variable
    os.environ['PAW_SERVER_TYPE'] = args.server_type

    run_server(
        database_path=args.database,
        temp_dir=args.save_dir,
        port=args.port,
        host=args.wallabag_host,
        username=args.wallabag_username,
        password=args.wallabag_password,
        clientid=args.wallabag_clientid,
        secret=args.wallabag_secret
    )


if __name__ == '__main__':
    main()
