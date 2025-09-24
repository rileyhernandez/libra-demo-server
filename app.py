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
from flask import Flask, jsonify, Response, request, render_template_string
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

        @self.app.route('/')
        def dashboard():
            """Main dashboard page"""
            return render_template_string(self._get_dashboard_template())

        @self.app.route('/health')
        def health_check():
            """Health check endpoint"""
            return jsonify({
                'status': 'healthy',
                'timestamp': datetime.now().isoformat(),
                'monitoring': self.monitor.running
            })

        @self.app.route('/api/recent')
        def get_recent():
            """Get recent records API"""
            limit = request.args.get('limit', 100, type=int)
            limit = min(limit, 1000)  # Cap at 1000 records

            records = self.monitor.get_recent_records(limit)
            return jsonify({
                'records': records,
                'count': len(records),
                'timestamp': datetime.now().isoformat()
            })

        @self.app.route('/api/stream')
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

        @self.app.route('/api/latest')
        def get_latest():
            """Get the single most recent record API"""
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

    def _get_dashboard_template(self):
        """Return the HTML template for the dashboard"""
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Libra Scale Monitor</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: #333;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }

        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }

        .header h1 {
            font-size: 2.5rem;
            font-weight: 300;
            margin-bottom: 10px;
        }

        .status-bar {
            display: flex;
            gap: 20px;
            justify-content: center;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }

        .status-item {
            background: rgba(255, 255, 255, 0.9);
            padding: 15px 25px;
            border-radius: 10px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
            text-align: center;
            min-width: 150px;
        }

        .status-item h3 {
            font-size: 0.9rem;
            color: #666;
            margin-bottom: 5px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .status-item .value {
            font-size: 1.8rem;
            font-weight: bold;
            color: #4f46e5;
        }

        .connection-status {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }

        .connection-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #ef4444;
            animation: pulse 2s infinite;
        }

        .connection-dot.connected {
            background: #22c55e;
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .data-section {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 15px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
            overflow: hidden;
            margin-bottom: 20px;
        }

        .section-header {
            background: linear-gradient(90deg, #4f46e5, #7c3aed);
            color: white;
            padding: 20px 25px;
            font-size: 1.2rem;
            font-weight: 600;
        }

        .latest-reading {
            padding: 30px;
            text-align: center;
            border-bottom: 1px solid #e5e7eb;
        }

        .latest-reading.no-data {
            color: #6b7280;
            font-style: italic;
        }

        .reading-ingredient {
            font-size: 2rem;
            font-weight: bold;
            color: #1f2937;
            margin-bottom: 10px;
        }

        .reading-amount {
            font-size: 3rem;
            font-weight: 300;
            color: #4f46e5;
            margin-bottom: 15px;
        }

        .reading-details {
            display: flex;
            justify-content: center;
            gap: 30px;
            flex-wrap: wrap;
            margin-top: 20px;
        }

        .reading-detail {
            text-align: center;
        }

        .reading-detail .label {
            font-size: 0.8rem;
            color: #6b7280;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 5px;
        }

        .reading-detail .value {
            font-size: 1rem;
            color: #374151;
            font-weight: 500;
        }

        .action-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .action-starting { background: #dbeafe; color: #1e40af; }
        .action-heartbeat { background: #dcfce7; color: #166534; }
        .action-offline { background: #fee2e2; color: #dc2626; }

        .logs-table {
            width: 100%;
            border-collapse: collapse;
        }

        .logs-table th {
            background: #f8fafc;
            padding: 15px 20px;
            text-align: left;
            font-weight: 600;
            color: #374151;
            border-bottom: 2px solid #e5e7eb;
            font-size: 0.9rem;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }

        .logs-table td {
            padding: 15px 20px;
            border-bottom: 1px solid #f3f4f6;
            font-size: 0.9rem;
        }

        .logs-table tr:hover {
            background: #f9fafb;
        }

        .logs-container {
            max-height: 600px;
            overflow-y: auto;
        }

        .logs-container::-webkit-scrollbar {
            width: 8px;
        }

        .logs-container::-webkit-scrollbar-track {
            background: #f1f5f9;
        }

        .logs-container::-webkit-scrollbar-thumb {
            background: #cbd5e1;
            border-radius: 4px;
        }

        .timestamp {
            font-family: 'Courier New', monospace;
            color: #6b7280;
        }

        .loading {
            text-align: center;
            padding: 40px;
            color: #6b7280;
        }

        .spinner {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid #f3f4f6;
            border-radius: 50%;
            border-top-color: #4f46e5;
            animation: spin 1s ease-in-out infinite;
            margin-right: 10px;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @media (max-width: 768px) {
            .container {
                padding: 10px;
            }

            .header h1 {
                font-size: 2rem;
            }

            .status-bar {
                flex-direction: column;
                align-items: center;
            }

            .reading-details {
                flex-direction: column;
                gap: 15px;
            }

            .logs-table {
                font-size: 0.8rem;
            }

            .logs-table th,
            .logs-table td {
                padding: 10px 15px;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Scale Monitor</h1>
            <p>Real-time inventory monitoring dashboard</p>
        </div>

        <div class="status-bar">
            <div class="status-item">
                <h3>Connection</h3>
                <div class="connection-status">
                    <div class="connection-dot" id="connectionDot"></div>
                    <span id="connectionStatus">Connecting...</span>
                </div>
            </div>
            <div class="status-item">
                <h3>Total Records</h3>
                <div class="value" id="totalRecords">-</div>
            </div>
            <div class="status-item">
                <h3>Last Updated</h3>
                <div class="value" id="lastUpdated">-</div>
            </div>
        </div>

        <div class="data-section">
            <div class="section-header">
                📊 Latest Reading
            </div>
            <div class="latest-reading" id="latestReading">
                <div class="loading">
                    <div class="spinner"></div>
                    Loading latest reading...
                </div>
            </div>
        </div>

        <div class="data-section">
            <div class="section-header">
                📋 Recent Activity
            </div>
            <div class="logs-container">
                <table class="logs-table">
                    <thead>
                        <tr>
                            <th>Time</th>
                            <th>Ingredient</th>
                            <th>Amount</th>
                            <th>Location</th>
                            <th>Action</th>
                            <th>Scale</th>
                        </tr>
                    </thead>
                    <tbody id="logsTableBody">
                        <tr>
                            <td colspan="6" class="loading">
                                <div class="spinner"></div>
                                Loading recent activity...
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let connectionStatus = 'disconnected';

        function updateConnectionStatus(status) {
            const dot = document.getElementById('connectionDot');
            const statusText = document.getElementById('connectionStatus');

            if (status === 'connected') {
                dot.classList.add('connected');
                statusText.textContent = 'Connected';
                connectionStatus = 'connected';
            } else {
                dot.classList.remove('connected');
                statusText.textContent = 'Disconnected';
                connectionStatus = 'disconnected';
            }
        }

        function formatTimestamp(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleTimeString();
        }

        function formatDate(timestamp) {
            const date = new Date(timestamp);
            return date.toLocaleDateString();
        }

        function formatAmount(amount) {
            if (amount === 0) return '0g';
            return parseFloat(amount).toLocaleString() + 'g';
        }

        function getActionBadge(action) {
            const className = `action-${action.toLowerCase()}`;
            return `<span class="action-badge ${className}">${action}</span>`;
        }

        function updateLatestReading(record) {
            const container = document.getElementById('latestReading');

            if (!record) {
                container.innerHTML = '<div class="no-data">No data available</div>';
                return;
            }

            container.innerHTML = `
                <div class="reading-ingredient">${record.ingredient}</div>
                <div class="reading-amount">${formatAmount(record.amount)}</div>
                <div class="reading-details">
                    <div class="reading-detail">
                        <div class="label">Location</div>
                        <div class="value">${record.location}</div>
                    </div>
                    <div class="reading-detail">
                        <div class="label">Scale</div>
                        <div class="value">${record.number}</div>
                    </div>
                    <div class="reading-detail">
                        <div class="label">Action</div>
                        <div class="value">${getActionBadge(record.action)}</div>
                    </div>
                    <div class="reading-detail">
                        <div class="label">Model</div>
                        <div class="value">${record.model}</div>
                    </div>
                </div>
            `;
        }

        function updateLogsTable(records) {
            const tbody = document.getElementById('logsTableBody');

            if (!records || records.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" class="loading">No recent activity</td></tr>';
                return;
            }

            tbody.innerHTML = records.map(record => `
                <tr>
                    <td class="timestamp">${formatTimestamp(record.timestamp)}</td>
                    <td><strong>${record.ingredient}</strong></td>
                    <td>${formatAmount(record.amount)}</td>
                    <td>${record.location}</td>
                    <td>${getActionBadge(record.action)}</td>
                    <td>${record.number}</td>
                </tr>
            `).join('');
        }

        function loadRecentData() {
            fetch('/api/recent?limit=50')
                .then(response => response.json())
                .then(data => {
                    updateConnectionStatus('connected');
                    document.getElementById('totalRecords').textContent = data.count;
                    document.getElementById('lastUpdated').textContent = formatTimestamp(data.timestamp);

                    if (data.records && data.records.length > 0) {
                        updateLatestReading(data.records[0]);
                        updateLogsTable(data.records);
                    }
                })
                .catch(error => {
                    console.error('Error loading data:', error);
                    updateConnectionStatus('disconnected');
                });
        }

        // Load initial data
        loadRecentData();

        // Refresh data every 2 seconds
        setInterval(loadRecentData, 2000);

        // Update last updated time every second
        setInterval(() => {
            if (connectionStatus === 'connected') {
                const now = new Date();
                document.getElementById('lastUpdated').textContent = formatTimestamp(now.toISOString());
            }
        }, 1000);
    </script>
</body>
</html>
        '''

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
    parser.add_argument('--db', default='libra_logs.db', help='Path to SQLite database file')
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
    logger.info(f"  GET / - Web dashboard")
    logger.info(f"  GET /health - Server health check")
    logger.info(f"  GET /api/recent?limit=N - Get recent records (default: 100)")
    logger.info(f"  GET /api/latest - Get most recent record")
    logger.info(f"  GET /api/stream - Server-sent events stream (real-time)")

    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")


if __name__ == '__main__':
    main()