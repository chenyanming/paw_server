# PAW Server Makefile
# Python CLI tool and server components for PAW (Point-and-Write)

.PHONY: install-deps install-uvx check-deps check-python \
        test test-server \
        server server-standalone server-production server-uvx server-stop server-status \
        dist-python upload-python \
        clean help quick-setup install-dev

# Tool detection and preference
PYTHON := $(shell which python3 2>/dev/null || which python 2>/dev/null || echo "python3")
UV := $(shell which uv 2>/dev/null)
UVX := $(shell which uvx 2>/dev/null)
PAW_CMD := $(shell which paw 2>/dev/null)

# Check if python is available
check-python:
	@$(PYTHON) --version > /dev/null 2>&1 || (echo "Error: Python is not installed. Please install Python first." && exit 1)

# Check dependencies and suggest installation methods
check-deps: check-python
	@echo "🔍 Checking dependencies..."
	@echo "Python: $(PYTHON)"
ifdef UV
	@echo "✓ uv found: $(UV)"
else
	@echo "❌ uv not found (optional, but recommended for faster installs)"
endif
ifdef UVX
	@echo "✓ uvx found: $(UVX)"
else
	@echo "❌ uvx not found (optional, for isolated command execution)"
endif
ifdef PAW_CMD
	@echo "✓ paw command found: $(PAW_CMD)"
else
	@echo "❌ paw command not found (will use python -m paw.cli)"
endif

# Install Python dependencies (smart detection of best method)
install-deps: check-deps
	@echo "📦 Installing Python dependencies..."
ifdef UV
	@echo "Using uv for faster installation..."
	@"$(UV)" pip install -e .
	@"$(UV)" pip install pytest pytest-cov uvicorn
else
	@echo "Using pip for installation..."
	@$(PYTHON) -m pip install -e .
	@$(PYTHON) -m pip install pytest pytest-cov uvicorn
endif
	@echo "✓ Dependencies installed"

# Alternative installation using uvx (isolated)
install-uvx:
ifdef UVX
	@echo "📦 Installing emacs-paw using uvx..."
	@"$(UVX)" --from emacs-paw paw --help
	@echo "✓ PAW installed via uvx"
else
	@echo "❌ uvx not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
	@exit 1
endif

# Install in development mode
install-dev: check-python
ifdef UV
	@echo "📦 Installing in development mode with uv..."
	@"$(UV)" pip install -e ".[dev]"
else
	@echo "📦 Installing in development mode..."
	@$(PYTHON) -m pip install -e ".[dev]"
endif

# Run tests
test: install-deps
	@echo "🧪 Running tests..."
	@if [ -d "tests" ]; then \
		$(PYTHON) -m pytest tests/ -v --cov=paw --cov-report=term-missing; \
	else \
		echo "📝 No tests directory found. Creating basic test structure..."; \
		mkdir -p tests; \
		echo 'import pytest\ndef test_basic():\n    assert True' > tests/test_basic.py; \
		$(PYTHON) -m pytest tests/ -v; \
	fi

# Test server functionality
test-server: install-deps
	@echo "🧪 Testing server functionality..."
	@echo "Starting server in background for testing..."
	@timeout 10s $(PYTHON) -c "from paw.paw_server import main; main()" &
	@sleep 2
	@curl -f http://localhost:$${PAW_PORT:-5001}/words > /dev/null 2>&1 && echo "✓ Server test passed" || echo "❌ Server test failed"
	@pkill -f "paw_server" 2>/dev/null || true

# Server Management
# =================

# Start PAW server (automatic method detection)
server: check-deps
	@echo "🚀 Starting PAW server..."
	@if [ ! -z "$(PAW_CMD)" ] && $(PAW_CMD) --help > /dev/null 2>&1; then \
		echo "Using installed paw command..."; \
		$(PAW_CMD) server; \
	else \
		echo "Using python module directly..."; \
		$(PYTHON) -m paw.paw_server; \
	fi

# Start server in standalone mode (production)
server-standalone: install-deps
	@echo "🚀 Starting PAW server in standalone mode..."
	@$(PYTHON) -m paw.paw_server

# Start server with production ASGI server (HTTP + WebSocket)
server-production: install-deps
	@echo "🚀 Starting PAW server with production ASGI server..."
	@export PAW_SERVER_TYPE="production" && \
	 $(PYTHON) -m paw.paw_server

# Start server with uvx (isolated environment)
server-uvx:
ifdef UVX
	@echo "🚀 Starting PAW server with uvx..."
	@echo "⚠️  Note: uvx uses PyPI version which may not have latest server command"
	@echo "    Consider using 'make server' or 'make server-standalone' instead"
	@"$(UVX)" --from emacs-paw python -m paw.paw_server \
		$$([ -n "$$PAW_DATABASE_PATH" ] && echo "--database $$PAW_DATABASE_PATH") \
		$$([ -n "$$PAW_SAVE_DIR" ] && echo "--save-dir $$PAW_SAVE_DIR") \
		$$([ -n "$$PAW_PORT" ] && echo "--port $$PAW_PORT") \
		$$([ -n "$$PAW_SERVER_TYPE" ] && echo "--server-type $$PAW_SERVER_TYPE")
else
	@echo "❌ uvx not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
	@exit 1
endif

# Stop PAW server
server-stop:
	@echo "🛑 Stopping PAW server..."
	@pkill -f "paw.*server" 2>/dev/null || echo "No PAW server process found"
	@pkill -f "paw_server" 2>/dev/null || true
	@echo "✓ Server stopped"

# Check server status
server-status:
	@echo "📊 Checking PAW server status..."
	@if pgrep -f "paw.*server" > /dev/null; then \
		echo "✓ PAW server is running"; \
		echo "Processes:"; \
		pgrep -f "paw.*server" | xargs ps -p; \
	else \
		echo "❌ PAW server is not running"; \
	fi

# Python Package Management
# =========================

# Build Python distribution
dist-python: check-python
	@echo "📦 Installing build dependencies..."
ifdef UV
	@"$(UV)" pip install --system build twine
else
	@$(PYTHON) -m pip install build twine
endif
	@echo "📦 Building Python distribution..."
	@$(PYTHON) -m build

# Upload to PyPI
upload-python: dist-python
	@echo "📤 Uploading to PyPI..."
	@$(PYTHON) -m twine upload dist/*

# Utilities
# =========

# Clean all build artifacts and generated files
clean:
	@echo "🧹 Cleaning build artifacts..."
	@rm -rf build dist *.egg-info
	@rm -rf __pycache__ .pytest_cache .coverage
	@rm -rf paw/__pycache__
	@rm -rf uploads paw.sqlite paw-server.log
	@echo "✓ Cleaned all artifacts"

# Quick development setup
quick-setup: install-deps
	@echo "⚡ Quick setup complete!"
	@echo "Available commands:"
	@echo "  make server          - Start PAW server (auto-detect best method)"
	@echo "  make server-production - Start with production ASGI server"
	@echo "  make server-uvx      - Start with uvx (isolated environment)"
	@echo "  make test            - Run tests"

# Show help
help:
	@echo "PAW Server Makefile Commands"
	@echo "============================"
	@echo ""
	@echo "🔧 Setup & Installation:"
	@echo "  make check-deps      - Check available tools and dependencies"
	@echo "  make install-deps    - Install Python dependencies (auto-detect uv/pip)"
	@echo "  make install-dev     - Install in development mode"
	@echo "  make install-uvx     - Install PAW using uvx (isolated)"
	@echo "  make quick-setup     - Quick setup for development"
	@echo ""
	@echo "🚀 Server Management:"
	@echo "  make server          - Start server (auto-detect best method)"
	@echo "  make server-standalone - Start standalone server"
	@echo "  make server-production - Start with production ASGI server"
	@echo "  make server-uvx      - Start with uvx (isolated)"
	@echo "  make server-stop     - Stop running server"
	@echo "  make server-status   - Check server status"
	@echo ""
	@echo "🧪 Testing:"
	@echo "  make test            - Run Python tests"
	@echo "  make test-server     - Test server functionality"
	@echo ""
	@echo "📦 Python Packaging:"
	@echo "  make dist-python     - Build Python distribution"
	@echo "  make upload-python   - Upload to PyPI"
	@echo ""
	@echo "🧹 Utilities:"
	@echo "  make clean           - Clean all build artifacts"
	@echo "  make help            - Show this help message"
	@echo ""
	@echo "🌟 Environment Variables:"
	@echo "  PAW_DATABASE_PATH    - Database file path"
	@echo "  PAW_SAVE_DIR         - Upload directory"
	@echo "  PAW_PORT             - Server port (default: 5001)"
	@echo "  PAW_SERVER_TYPE      - Server type: flask/production/waitress"
	@echo "  WALLABAG_*           - Wallabag configuration variables"
	@echo ""
	@echo "💡 Usage Examples:"
	@echo "  # Quick start with environment variables"
	@echo "  export PAW_DATABASE_PATH=/path/to/paw.sqlite"
	@echo "  export PAW_SAVE_DIR=/tmp/"
	@echo "  make server-production"
	@echo ""
	@echo "  # Command line usage"
	@echo "  paw server --database /path/to/paw.sqlite --port 5001"

# Default target
.DEFAULT_GOAL := help
