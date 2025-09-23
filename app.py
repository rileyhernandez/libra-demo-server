#!/usr/bin/env python3
"""
SQLite Database Monitor Server

Monitors a SQLite database for new entries and serves them via HTTP endpoints.
Supports real-time streaming of new data and querying historical data.
"""

import sqlite3
import json
import time
import threading
from datetime import datetime
from flask import Flask, jsonify, Response, request
from flask_cors import CORS
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class LibraLogMonitor:
    def __init__(self, db_path, poll_interval=1.0):
        self.db_path = db_path
        self.poll_interval = poll_interval
        self.last_rowid = 0
        self.running = False
        self.new_data_callbacks = []

    def start_monitoring(self):
        """Start monitoring the database in a separate thread"""
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"Started monitoring database: {self.db_path}")

    def stop_monitoring(self):
        """Stop monitoring the database"""
        self.running = False
        logger.info("Stopped monitoring database")

    def add_callback(self, callback):
        """Add a callback function to be called when new data arrives"""
        self.new_data_callbacks.append(callback)

    def _monitor_loop(self):
        """Main monitoring loop"""
        # Get the initial last rowid
        self.last_rowid = self._get_last_rowid()
        logger.info(f"Starting monitor from rowid: {self.last_rowid}")

        while self.running:
            try:
                new_records = self._check_for_new_records()
                if new_records:
                    logger.info(f"Found {len(new_records)} new records")
                    for callback in self.new_data_callbacks:
                        try:
                            callback(new_records)
                        except Exception as e:
                            logger.error(f"Error in callback: {e}")

                time.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(self.poll_interval)

    def _get_last_rowid(self):
        """Get the highest rowid from the database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT MAX(rowid) FROM libra_logs")
                result = cursor.fetchone()[0]
                return result if result is not None else 0
        except sqlite3.Error as e:
            logger.error(f"Database error getting last rowid: {e}")
            return 0

    def _check_for_new_records(self):
        """Check for new records since last check"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row  # Enable column access by name
                cursor = conn.cursor()

                cursor.execute("""
                               SELECT rowid, model, number, timestamp, action, amount, location, ingredient, synced
                               FROM libra_logs
                               WHERE rowid > ?
                               ORDER BY rowid ASC
                               """, (self.last_rowid,))

                records = cursor.fetchall()

                if records:
                    # Update last_rowid to the highest rowid we've seen
                    self.last_rowid = records[-1]['rowid']

                    # Convert to list of dictionaries
                    return [dict(record) for record in records]

                return []

        except sqlite3.Error as e:
            logger.error(f"Database error checking for new records: {e}")
            return []

    def get_recent_records(self, limit=100):
        """Get the most recent records"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                               SELECT rowid, model, number, timestamp, action, amount, location, ingredient, synced
                               FROM libra_logs
                               ORDER BY rowid DESC
                                   LIMIT ?
                               """, (limit,))

                records = cursor.fetchall()
                return [dict(record) for record in records]

        except sqlite3.Error as e:
            logger.error(f"Database error getting recent records: {e}")
            return []


class LibraLogServer:
    def __init__(self, db_path, port=5000, host='0.0.0.0'):
        self.app = Flask(__name__)
        CORS(self.app)  # Enable CORS for web clients

        self.monitor = LibraLogMonitor(db_path)
        self.port = port
        self.host = host

        # Store for Server-Sent Events clients
        self.sse_clients = set()

        # Set up routes
        self._setup_routes()

        # Add callback for new data
        self.monitor.add_callback(self._on_new_data)

    def _setup_routes(self):
        """Set up Flask routes"""

        @self.app.route('/health')
        def health_check():
            """Health check endpoint"""
            return jsonify({
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'monitoring': self.monitor.running
            })

        @self.app.route('/recent')
        def get_recent():
            """Get recent records"""
            limit = request.args.get('limit', 100, type=int)
            limit = min(limit, 1000)  # Cap at 1000 records

            records = self.monitor.get_recent_records(limit)
            return jsonify({
                'records': records,
                'count': len(records),
                'timestamp': datetime.now().isoformat()
            })

        @self.app.route('/stream')
        def stream_data():
            """Server-Sent Events endpoint for real-time data"""

            def event_stream():
                # Send initial connection message
                yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.now().isoformat()})}\n\n"

                # Keep connection alive
                while True:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"
                    time.sleep(30)  # Send heartbeat every 30 seconds

            response = Response(event_stream(), mimetype='text/event-stream')
            response.headers['Cache-Control'] = 'no-cache'
            response.headers['Connection'] = 'keep-alive'
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        @self.app.route('/latest')
        def get_latest():
            """Get the single most recent record"""
            records = self.monitor.get_recent_records(1)
            if records:
                return jsonify({
                    'record': records[0],
                    'timestamp': datetime.now().isoformat()
                })
            else:
                return jsonify({
                    'record': None,
                    'message': 'No records found',
                    'timestamp': datetime.now().isoformat()
                }), 404

    def _on_new_data(self, new_records):
        """Callback for when new data is detected"""
        # Log new data
        for record in new_records:
            logger.info(f"New record: {record['ingredient']} = {record['amount']} at {record['location']}")

        # Here you could add additional processing, such as:
        # - Sending notifications
        # - Triggering webhooks
        # - Broadcasting via WebSocket
        # - Storing in cache

        # For now, we'll just broadcast via Server-Sent Events if you implement that
        pass

    def start(self):
        """Start the monitoring and web server"""
        logger.info(f"Starting Libra Log Server on {self.host}:{self.port}")

        # Start database monitoring
        self.monitor.start_monitoring()

        # Start Flask server
        try:
            self.app.run(host=self.host, port=self.port, debug=False, threaded=True)
        except KeyboardInterrupt:
            logger.info("Shutting down server...")
        finally:
            self.monitor.stop_monitoring()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Libra Log Database Monitor Server')
    parser.add_argument('--db', default='data.db', help='Path to SQLite database file')
    parser.add_argument('--port', type=int, default=5000, help='Port to serve on')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--poll-interval', type=float, default=1.0, help='Database polling interval in seconds')

    args = parser.parse_args()

    # Create and start server
    server = LibraLogServer(args.db, args.port, args.host)
    server.monitor.poll_interval = args.poll_interval

    logger.info("=== Libra Log Monitor Server ===")
    logger.info(f"Database: {args.db}")
    logger.info(f"Server: http://{args.host}:{args.port}")
    logger.info(f"Poll interval: {args.poll_interval}s")
    logger.info("Available endpoints:")
    logger.info(f"  GET /health - Server health check")
    logger.info(f"  GET /recent?limit=N - Get recent records (default: 100)")
    logger.info(f"  GET /latest - Get most recent record")
    logger.info(f"  GET /stream - Server-sent events stream (real-time)")

    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")


if __name__ == '__main__':
    main()