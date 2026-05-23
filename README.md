# paw_server - paw Python Command Line Interface

[paw server](https://github.com/chenyanming/paw_server) eval commands on Emacs using emacsclient or org-protocol

This repository contains the Python CLI tool and server components that work with [paw.el](https://github.com/chenyanming/paw), providing advanced annotation and language learning tools for Emacs.

## Overview

`paw_server` is the Python backend for the PAW (Point-And-Write) system, providing:

- Command line interface for dictionary searches and language processing
- HTTP server for Emacs integration
- Optional browser media control/status bridge for the PAW browser extension
- Support for multiple languages (English, Japanese, Chinese)
- Database-driven annotation and vocabulary management
- Integration with external services like Wallabag

## Installation

### Python Dependencies

Install the PAW CLI tool:

```sh
pip install emacs-paw
```

Additional NLTK data:

```sh
python -m nltk.downloader stopwords
python -m nltk.downloader punkt
```

## Usage

### Available Commands

- `run_server`: Start the PAW server for handling annotation requests (designed for Emacs integration)
- `server`: Start the PAW server in standalone mode with enhanced features
- `en_search`: Search in English dictionaries
- `ja_search`: Search in Japanese dictionaries
- `ja_segment`: Japanese text segmentation
- `check_language`: Detect language of given text

### Server Operations

#### Start PAW Server (Emacs Integration Mode)

```sh
paw run_server --database /home/user/org/paw.sqlite \
               --save-dir /tmp/source.html \
               --port 5001 \
               --wallabag-host https://example.com \
               --wallabag-username username \
               --wallabag-password password \
               --wallabag-clientid clientid \
               --wallabag-secret secret
```

#### Start PAW Server (Standalone Mode)

Using command line arguments:

```sh
paw server --database /home/user/org/paw.sqlite \
           --save-dir /tmp/ \
           --port 5001 \
           --server-type production \
           --wallabag-host https://example.com \
           --wallabag-username username \
           --wallabag-password password \
           --wallabag-clientid clientid \
           --wallabag-secret secret
```

Using environment variables (recommended for production):

```sh
export PAW_DATABASE_PATH="/home/user/org/paw.sqlite"
export PAW_SAVE_DIR="/tmp/"
export PAW_PORT="5001"
export PAW_SERVER_TYPE="production"
export PAW_LOG_LEVEL="INFO"
export PAW_ACCESS_LOG="false"
export WALLABAG_HOST="https://example.com"
export WALLABAG_USERNAME="your_username"
export WALLABAG_PASSWORD="your_password"
export WALLABAG_CLIENTID="your_client_id"
export WALLABAG_SECRET="your_client_secret"

paw server
```

**Server Options:**
- `--database`: Path to SQLite database file (env: PAW_DATABASE_PATH)
- `--save-dir`: Directory to save files (env: PAW_SAVE_DIR)
- `--port`: Server port (env: PAW_PORT, default: 5001)
- `--server-type`: Server type - 'flask', 'production', or 'waitress' (env: PAW_SERVER_TYPE, default: flask)
- `--wallabag-*`: Wallabag configuration (env: WALLABAG_HOST, WALLABAG_USERNAME, etc.)

### Browser Media Bridge

`paw_server` can proxy browser media status and controls between local clients and the PAW browser extension.

The bridge is opt-in from the browser extension popup/options. When enabled, the extension opens a WebSocket to:

```text
ws://localhost:5001/media/ws
```

Emacs and other local clients send requests to:

```text
POST http://localhost:5001/media/request
```

Example status request:

```sh
curl -s http://localhost:5001/media/request \
  -H 'Content-Type: application/json' \
  -d '{"action":"status","targetUrl":"https://www.netflix.com/watch/12345","timeoutMs":800}'
```

Example control request:

```sh
curl -s http://localhost:5001/media/request \
  -H 'Content-Type: application/json' \
  -d '{"action":"toggle","targetUrl":"https://www.netflix.com/watch/12345","timeoutMs":800}'
```

Supported actions:

- `status`
- `play`
- `pause`
- `toggle`
- `seekRelative` with `deltaMs`
- `seekAbsolute` with `positionMs`

The response always has a media-shaped JSON body so polling clients can stay simple:

```json
{
  "ok": true,
  "source": "fresh",
  "stale": false,
  "provider": "netflix",
  "mediaId": "browser:123:0:abcdef",
  "url": "https://www.netflix.com/watch/12345",
  "title": "Example",
  "currentTimeMs": 10000,
  "durationMs": 600000,
  "remainingMs": 590000,
  "paused": false,
  "playbackRate": 1.0,
  "canControl": true,
  "updatedAtMs": 1710000000000,
  "lastError": null
}
```

If the extension is unavailable, times out, or no matching media tab is found, the server returns the last cached status for the same `targetUrl`. If there is no cached status, it returns an empty paused status with `source: "empty"` and `stale: true`. Browser media requests bound to a `targetUrl` do not fall back to unrelated tabs.

Run the server in production mode for the media WebSocket:

```sh
export PAW_SERVER_TYPE=production
export PAW_LOG_LEVEL=DEBUG
export PAW_ACCESS_LOG=false
python3 -m paw.cli server --server-type production
```

`production` uses Uvicorn/ASGI so normal HTTP endpoints and `/media/ws` share one port. The legacy `waitress` mode is still available for HTTP-only WSGI deployments, but it does not support `/media/ws`.

### Dictionary Operations

#### English Dictionary Search

```sh
paw en_search /home/user/org/stardict.db MATCH hello \
              --tag "" \
              --wordlists /home/user/org/5000.csv \
              --known-words-files /home/user/org/eudic.csv,/home/user/org/english.txt
```

#### Japanese Dictionary Search

```sh
paw ja_search /home/user/org/japanese.db MATCH "海外の大企業は" \
              --tag "" \
              --wordlist /home/user/org/蓝宝书日语文法.csv \
              --known-words-files /home/user/org/japanese.txt
```

#### Japanese Text Segmentation

```sh
paw ja_segment "実在の女性を骨抜きにしたオスたちの話だけを紹介しており"
```

Returns JSON with segmentation details including surface form, base form, and reading:

```json
[
  {
    "surface": "実在",
    "base_form": "実在",
    "reading": "ジツザイ"
  },
  {
    "surface": "の",
    "base_form": "の",
    "reading": "ノ"
  },
  {
    "surface": "女性",
    "base_form": "女性",
    "reading": "ジョセイ"
  }
]
```

- `surface`: for segmentation
- `base_form`: for dictionary checking
- `reading`: for online sound service

#### Language Detection

```sh
paw check_language --languages "english,chinese,japanese" \
                   --text "これは日本語の文です"
```

## Production Deployment

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PAW_DATABASE_PATH` | Path to SQLite database | None |
| `PAW_SAVE_DIR` | Directory to save files | `/tmp` |
| `PAW_PORT` | Server port | `5001` |
| `PAW_SERVER_TYPE` | Server type (flask/production/waitress) | `flask` |
| `PAW_LOG_LEVEL` | Python logging level | `INFO` |
| `PAW_ACCESS_LOG` | Enable Uvicorn per-request access logs in production mode | `false` |
| `WALLABAG_HOST` | Wallabag server URL | None |
| `WALLABAG_USERNAME` | Wallabag username | None |
| `WALLABAG_PASSWORD` | Wallabag password | None |
| `WALLABAG_CLIENTID` | Wallabag client ID | None |
| `WALLABAG_SECRET` | Wallabag client secret | None |

### Production Deployment

```sh
# Set environment variables
export PAW_DATABASE_PATH="/path/to/your/paw.sqlite"
export PAW_SAVE_DIR="/var/www/paw/uploads/"
export PAW_PORT="5001"
export PAW_SERVER_TYPE="production"
export PAW_ACCESS_LOG="false"

# Configure Wallabag (optional)
export WALLABAG_HOST="https://your-wallabag.com"
export WALLABAG_USERNAME="your_username"
export WALLABAG_PASSWORD="your_password"
export WALLABAG_CLIENTID="your_client_id"
export WALLABAG_SECRET="your_client_secret"

# Run the server
paw server
```

`production` runs with Uvicorn/ASGI and supports both HTTP endpoints and the browser media WebSocket. Use `PAW_SERVER_TYPE=waitress` only if you need the old HTTP-only WSGI runtime.

## Features

- **Enhanced Stability**: Improved error handling and automatic database reconnection
- **Environment Variable Support**: All configuration via environment variables
- **Production Server Support**: Uvicorn/ASGI for HTTP and browser media WebSocket support; Waitress WSGI remains available for HTTP-only deployments
- **Graceful Shutdown**: Proper cleanup on server shutdown
- **Comprehensive Logging**: Logging to both console and file
- **Thread Safety**: Safe concurrent database access
- **Multi-language Support**: English, Japanese, and Chinese language processing
- **Dictionary Integration**: Support for various dictionary formats and sources

## Integration with PAW.el

This server component is designed to work seamlessly with [paw.el](https://github.com/chenyanming/paw), the Emacs annotation and language learning system. The server provides:

- Real-time dictionary lookups
- Language processing services
- Annotation storage and retrieval
- Wallabag integration for web content management

## Development

### Project Structure

```
paw_server/
├── paw/
│   ├── __init__.py
│   ├── cli.py              # Command line interface
│   ├── paw_server.py       # HTTP server implementation
│   ├── paw_ecdict.py       # English-Chinese dictionary support
│   ├── paw_jlpt.py         # Japanese language processing (JLPT)
│   └── paw_mecab.py        # MeCab integration for Japanese
├── pyproject.toml          # Project configuration
└── README.md               # This file
```

### Dependencies

- Python 3.10+
- Flask & Flask-CORS for web server
- Uvicorn and asgiref for production ASGI serving
- simple-websocket for Flask/local WebSocket serving
- Waitress for legacy HTTP-only WSGI serving
- NLTK for natural language processing
- MeCab and related Japanese processing libraries
- Requests for HTTP client functionality
- Lingua for language detection

## License

This project is licensed under the GNU General Public License v3.0.

## Author

Damon Chan

## Related Projects

- [paw.el](https://github.com/chenyanming/paw) - The main Emacs package for annotation and language learning
- [paw Browser extension](https://github.com/chenyanming/paw_browser_extension) - Chrome/Firefox extension for eval Emacs commands or org-protocol on browser
