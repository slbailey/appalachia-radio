"""
Lightweight HTTP status server for Appalachia Radio.

Phase 6: Built-in dashboard using only Python standard library.
Provides read-only status endpoints for monitoring.
"""

import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class StatusRequestHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for status endpoints.
    
    Uses injected callables to get data from PlayoutEngine and Playlog.
    This keeps the handler decoupled from engine types.
    """
    
    # These callables will be injected at startup
    get_health: Optional[Callable[[], object]] = None
    get_now_playing: Optional[Callable[[], object]] = None
    get_next_up: Optional[Callable[[], object]] = None
    get_recent_playlog: Optional[Callable[[int], list]] = None
    
    def _send_json(self, payload: object, status: int = 200) -> None:
        """
        Send JSON response.
        
        Args:
            payload: Object to serialize as JSON
            status: HTTP status code (default: 200)
        """
        try:
            json_str = json.dumps(payload, default=self._json_serializer, indent=2)
            json_bytes = json_str.encode('utf-8')
            
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(json_bytes)))
            self.end_headers()
            self.wfile.write(json_bytes)
        except Exception as e:
            logger.error(f"Error sending JSON response: {e}", exc_info=True)
            # Try to send error response
            try:
                error_payload = {"error": "Internal server error"}
                error_json = json.dumps(error_payload).encode('utf-8')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(error_json)))
                self.end_headers()
                self.wfile.write(error_json)
            except Exception:
                pass  # Give up if we can't even send error
    
    def _json_serializer(self, obj: object) -> str:
        """
        Custom JSON serializer for datetime and other types.
        
        Args:
            obj: Object to serialize
            
        Returns:
            JSON-serializable representation
        """
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")
    
    def do_GET(self) -> None:
        """
        Handle GET requests.
        
        Routes:
          /ping      -> {"status": "ok"}
          /status    -> {"health": ..., "now_playing": ..., "next_up": ...}
          /playlog   -> {"entries": [...]}
        """
        try:
            path = self.path.split('?')[0]  # Remove query string
            
            if path == '/ping':
                self._handle_ping()
            elif path == '/status':
                self._handle_status()
            elif path == '/playlog':
                self._handle_playlog()
            else:
                # 404 for unknown paths
                self._send_json({"error": "Not found"}, status=404)
        except Exception as e:
            logger.error(f"Error handling GET request: {e}", exc_info=True)
            self._send_json({"error": "Internal server error"}, status=500)
    
    def _handle_ping(self) -> None:
        """Handle /ping endpoint."""
        self._send_json({"status": "ok"})
    
    def _handle_status(self) -> None:
        """Handle /status endpoint."""
        payload = {}
        
        # Get health
        if StatusRequestHandler.get_health:
            try:
                health = StatusRequestHandler.get_health()
                # Convert dataclass to dict for JSON serialization
                if hasattr(health, '__dict__'):
                    payload["health"] = health.__dict__
                else:
                    payload["health"] = health
            except Exception as e:
                logger.error(f"Error getting health: {e}", exc_info=True)
                payload["health"] = None
        
        # Get now playing
        if StatusRequestHandler.get_now_playing:
            try:
                now_playing = StatusRequestHandler.get_now_playing()
                if now_playing is None:
                    payload["now_playing"] = None
                else:
                    # Convert dataclass to dict
                    if hasattr(now_playing, '__dict__'):
                        payload["now_playing"] = now_playing.__dict__
                    else:
                        payload["now_playing"] = now_playing
            except Exception as e:
                logger.error(f"Error getting now_playing: {e}", exc_info=True)
                payload["now_playing"] = None
        
        # Get next up
        if StatusRequestHandler.get_next_up:
            try:
                next_up = StatusRequestHandler.get_next_up()
                if next_up is None:
                    payload["next_up"] = None
                else:
                    # Convert dataclass to dict
                    if hasattr(next_up, '__dict__'):
                        payload["next_up"] = next_up.__dict__
                    else:
                        payload["next_up"] = next_up
            except Exception as e:
                logger.error(f"Error getting next_up: {e}", exc_info=True)
                payload["next_up"] = None
        
        self._send_json(payload)
    
    def _handle_playlog(self) -> None:
        """Handle /playlog endpoint."""
        if not StatusRequestHandler.get_recent_playlog:
            self._send_json({"entries": []})
            return
        
        try:
            # Get limit from query string (default: 50)
            limit = 50
            if '?' in self.path:
                query = self.path.split('?')[1]
                for param in query.split('&'):
                    if '=' in param:
                        key, value = param.split('=', 1)
                        if key == 'limit':
                            try:
                                limit = int(value)
                            except ValueError:
                                pass
            
            # Call the callable directly (it's a class variable, not a method)
            entries = StatusRequestHandler.get_recent_playlog(limit)
            
            # Convert entries to dicts for JSON serialization
            entries_dicts = []
            for entry in entries:
                if hasattr(entry, '__dict__'):
                    entries_dicts.append(entry.__dict__)
                else:
                    entries_dicts.append(entry)
            
            self._send_json({"entries": entries_dicts})
        except Exception as e:
            logger.error(f"Error getting playlog: {e}", exc_info=True)
            self._send_json({"entries": []})
    
    def log_message(self, format: str, *args: object) -> None:
        """Override to use our logger instead of stderr."""
        logger.debug(f"HTTP {format % args}")


def make_server(
    host: str,
    port: int,
    get_health: Callable[[], object],
    get_now_playing: Callable[[], object],
    get_next_up: Callable[[], object],
    get_recent_playlog: Callable[[int], list],
) -> HTTPServer:
    """
    Create and configure an HTTPServer instance with handlers bound
    to the runtime engine/playlog functions.
    
    Args:
        host: Host to bind to (e.g., "0.0.0.0")
        port: Port to bind to (e.g., 8080)
        get_health: Callable that returns health info
        get_now_playing: Callable that returns now playing info
        get_next_up: Callable that returns next up info
        get_recent_playlog: Callable that takes limit and returns playlog entries
        
    Returns:
        Configured HTTPServer instance
    """
    # Bind callables to handler class
    StatusRequestHandler.get_health = get_health
    StatusRequestHandler.get_now_playing = get_now_playing
    StatusRequestHandler.get_next_up = get_next_up
    StatusRequestHandler.get_recent_playlog = get_recent_playlog
    
    server = HTTPServer((host, port), StatusRequestHandler)
    logger.info(f"HTTP status server configured on {host}:{port}")
    return server


def run_in_background(server: HTTPServer) -> threading.Thread:
    """
    Start the HTTPServer in a background thread.
    
    Args:
        server: HTTPServer instance to run
        
    Returns:
        Thread object so the caller can join/stop if needed
    """
    def run_server():
        try:
            logger.info(f"HTTP status server starting on {server.server_address}")
            server.serve_forever()
        except Exception as e:
            logger.error(f"HTTP status server error: {e}", exc_info=True)
        finally:
            logger.info("HTTP status server stopped")
    
    thread = threading.Thread(target=run_server, name="DashboardServer", daemon=True)
    thread.start()
    return thread

