#!/usr/bin/env python3
"""
SQLite Database Monitor Server - Multi-Scale iPad Version

Monitors a SQLite database for new entries and serves them via HTTP endpoints.
Displays data from 4 different scales in separate columns optimized for iPad viewing.
"""

import sqlite3
import json
import time
import threading
from datetime import datetime
from flask import Flask, jsonify, Response, request, render_template_string
from flask_cors import CORS
import logging

scales = ['716710-0-0', '716710-1', '716710-2', '716710-3']
scale_units = {
    "scale_1": {"name": "bags", "size": 50},   # 1 bag = 50 g
    "scale_2": {"name": "boxes", "size": 200}, # 1 box = 200 g
    "scale_3": {"name": "g", "size": 1},       # plain grams
    "scale_4": {"name": "kg", "size": 1000},   # 1 kg = 1000 g
}

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
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("""
                               SELECT rowid, model, number, timestamp, action, amount, location, ingredient, synced
                               FROM libra_logs
                               WHERE rowid > ?
                               ORDER BY rowid ASC
                               """, (self.last_rowid,))

                records = cursor.fetchall()

                if records:
                    self.last_rowid = records[-1]['rowid']
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

    def get_records_by_scale(self, limit=50):
        """Get recent records grouped by scale number"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # Get recent records for each scale
                scales_data = {}
                for i, scale_num in enumerate(scales, start=1):
                    cursor.execute("""
                                   SELECT rowid, model, number, timestamp, action, amount, location, ingredient, synced
                                   FROM libra_logs
                                   WHERE number = ?
                                   ORDER BY rowid DESC
                                       LIMIT ?
                                   """, (scale_num, limit))

                    records = cursor.fetchall()
                    scales_data[f"scale_{i}"] = [dict(record) for record in records]

                return scales_data

        except sqlite3.Error as e:
            logger.error(f"Database error getting records by scale: {e}")
            return {f'{scales[i]}': None for i in scales}

    def get_latest_by_scale(self):
        """Get the latest record for each scale"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                latest_data = {}
                for i, scale_num in enumerate(scales, start=1):
                    # cursor.execute("""
                    #                SELECT rowid, model, number, timestamp, action, amount, location, ingredient, synced
                    #                FROM libra_logs
                    #                WHERE number = ?
                    #                ORDER BY rowid DESC
                    #                    LIMIT 1
                    #                """, (scale_num,))

                    cursor.execute("""
                                   SELECT rowid, model, number, timestamp, action, amount, location, ingredient, synced
                                   FROM libra_logs
                                   WHERE number = ?
                                   ORDER BY
                                       CASE
                                       WHEN action IN ('Served', 'Refilled') THEN 0
                                       ELSE 1
                                   END
                                   ,
                            rowid DESC
                        LIMIT 1
                                   """, (scale_num,))

                    record = cursor.fetchone()
                    latest_data[f"scale_{i}"] = dict(record) if record else None

                return latest_data
        except:
            return {f'{scales[i]}': None for i in scales}




class LibraLogServer:
    def __init__(self, db_path, port=5000, host='0.0.0.0'):
        self.app = Flask(__name__)
        CORS(self.app)

        self.monitor = LibraLogMonitor(db_path)
        self.port = port
        self.host = host

        self.sse_clients = set()
        self._setup_routes()
        self.monitor.add_callback(self._on_new_data)

    def _setup_routes(self):
        """Set up Flask routes"""

        @self.app.route('/')
        def dashboard():
            """Main dashboard page optimized for iPad"""
            return render_template_string(self._get_ipad_dashboard_template())

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
            limit = min(limit, 1000)

            records = self.monitor.get_recent_records(limit)
            return jsonify({
                'records': records,
                'count': len(records),
                'timestamp': datetime.now().isoformat()
            })

        @self.app.route('/api/scales')
        def get_scales_data():
            """Get data grouped by scale number"""
            limit = request.args.get('limit', 20, type=int)
            scales_data = self.monitor.get_records_by_scale(limit)

            return jsonify({
                'scales': scales_data,
                'timestamp': datetime.now().isoformat()
            })

        @self.app.route('/api/latest_scales')
        def get_latest_scales():
            """Get latest record for each scale"""
            latest_data = self.monitor.get_latest_by_scale()

            return jsonify({
                'latest': latest_data,
                'timestamp': datetime.now().isoformat()
            })

        @self.app.route('/api/stream')
        def stream_data():
            """Server-Sent Events endpoint for real-time data"""

            def event_stream():
                yield f"data: {json.dumps({'type': 'connected', 'timestamp': datetime.now().isoformat()})}\n\n"
                while True:
                    yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': datetime.now().isoformat()})}\n\n"
                    time.sleep(30)

            response = Response(event_stream(), mimetype='text/event-stream')
            response.headers['Cache-Control'] = 'no-cache'
            response.headers['Connection'] = 'keep-alive'
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

    def _on_new_data(self, new_records):
        """Callback for when new data is detected"""
        for record in new_records:
            logger.info(
                f"Scale {record['number']}: {record['ingredient']} = {record['amount']}g at {record['location']}")

    def _get_ipad_dashboard_template(self):
        """Return the HTML template optimized for iPad landscape viewing"""
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Libra Kitchen Monitor - iPad View</title>
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
            overflow-x: hidden;
        }

        .container {
            max-width: 100%;
            margin: 0;
            padding: 15px;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }

        .header {
            text-align: center;
            color: white;
            margin-bottom: 20px;
            flex-shrink: 0;
        }

        .header h1 {
            font-size: 2rem;
            font-weight: 300;
            margin-bottom: 5px;
        }

        .header p {
            font-size: 1rem;
            opacity: 0.9;
        }

        .status-bar {
            display: flex;
            gap: 15px;
            justify-content: center;
            margin-bottom: 20px;
            flex-shrink: 0;
        }

        .status-item {
            background: rgba(255, 255, 255, 0.9);
            padding: 10px 20px;
            border-radius: 8px;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
            text-align: center;
            min-width: 120px;
        }

        .status-item h3 {
            font-size: 0.75rem;
            color: #666;
            margin-bottom: 3px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .status-item .value {
            font-size: 1.2rem;
            font-weight: bold;
            color: #4f46e5;
        }

        .connection-status {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }

        .connection-dot {
            width: 10px;
            height: 10px;
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

        .scales-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 15px;
            flex-grow: 1;
            min-height: 0;
        }

        .scale-column {
            background: rgba(255, 255, 255, 0.95);
            border-radius: 12px;
            box-shadow: 0 8px 25px rgba(0, 0, 0, 0.1);
            backdrop-filter: blur(10px);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        .scale-header {
            background: linear-gradient(90deg, #4f46e5, #7c3aed);
            color: white;
            padding: 12px 15px;
            font-size: 1rem;
            font-weight: 600;
            text-align: center;
            flex-shrink: 0;
        }

        .scale-header.scale-1 { background: linear-gradient(90deg, #ef4444, #dc2626); }
        .scale-header.scale-2 { background: linear-gradient(90deg, #10b981, #059669); }
        .scale-header.scale-3 { background: linear-gradient(90deg, #f59e0b, #d97706); }
        .scale-header.scale-4 { background: linear-gradient(90deg, #8b5cf6, #7c3aed); }

        .latest-reading {
            padding: 15px;
            text-align: center;
            border-bottom: 1px solid #e5e7eb;
            flex-shrink: 0;
        }

        .latest-reading.no-data {
            color: #6b7280;
            font-style: italic;
            font-size: 0.9rem;
        }

        .reading-ingredient {
            font-size: 1.2rem;
            font-weight: bold;
            color: #1f2937;
            margin-bottom: 5px;
            line-height: 1.2;
        }

        .reading-amount {
            font-size: 1.8rem;
            font-weight: 300;
            color: #4f46e5;
            margin-bottom: 8px;
        }

        .reading-details {
            display: flex;
            justify-content: space-around;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 8px;
        }

        .reading-detail {
            text-align: center;
        }

        .reading-detail .label {
            font-size: 0.65rem;
            color: #6b7280;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 2px;
        }

        .reading-detail .value {
            font-size: 0.8rem;
            color: #374151;
            font-weight: 500;
        }

        .action-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 0.65rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }

        .action-heartbeat { background: #dbeafe; color: #1e40af; }
        .action-served { background: #dcfce7; color: #166534; }
        .action-refilled { background: #fee2e2; color: #dc2626; }

        .logs-container {
            flex-grow: 1;
            overflow-y: auto;
            min-height: 0;
        }

        .logs-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.75rem;
        }

        .logs-table th {
            background: #f8fafc;
            padding: 8px 10px;
            text-align: left;
            font-weight: 600;
            color: #374151;
            border-bottom: 1px solid #e5e7eb;
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.3px;
            position: sticky;
            top: 0;
        }

        .logs-table td {
            padding: 8px 10px;
            border-bottom: 1px solid #f3f4f6;
            vertical-align: top;
        }

        .logs-table tr:hover {
            background: #f9fafb;
        }

        .logs-container::-webkit-scrollbar {
            width: 6px;
        }

        .logs-container::-webkit-scrollbar-track {
            background: #f1f5f9;
        }

        .logs-container::-webkit-scrollbar-thumb {
            background: #cbd5e1;
            border-radius: 3px;
        }

        .timestamp {
            font-family: 'Courier New', monospace;
            color: #6b7280;
            font-size: 0.7rem;
        }

        .ingredient-name {
            font-weight: 600;
            color: #1f2937;
            font-size: 0.75rem;
        }

        .amount-value {
            font-weight: 500;
            color: #4f46e5;
        }

        .loading {
            text-align: center;
            padding: 20px;
            color: #6b7280;
            font-size: 0.8rem;
        }

        .spinner {
            display: inline-block;
            width: 16px;
            height: 16px;
            border: 2px solid #f3f4f6;
            border-radius: 50%;
            border-top-color: #4f46e5;
            animation: spin 1s ease-in-out infinite;
            margin-right: 8px;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        /* iPad specific optimizations */
        @media (max-width: 1024px) and (orientation: landscape) {
            .container {
                padding: 10px;
            }

            .header h1 {
                font-size: 1.8rem;
            }

            .scales-grid {
                gap: 10px;
            }
        }

        @media (max-width: 768px) {
            .scales-grid {
                grid-template-columns: repeat(2, 1fr);
                gap: 10px;
            }

            .header h1 {
                font-size: 1.5rem;
            }

            .status-bar {
                flex-wrap: wrap;
            }
        }

        @media (max-width: 480px) {
            .scales-grid {
                grid-template-columns: 1fr;
            }
        }
        .flash-update {
            animation: flash-bg 1s ease;
        }
        
        @keyframes flash-bg {
            0%   { background-color: #fef9c3; } /* light yellow */
            100% { background-color: transparent; }
        }

    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Libra Kitchen Monitor</h1>
            <p>Real-time inventory monitoring across 4 scales</p>
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
                <h3>Active Scales</h3>
                <div class="value" id="activeScales">-</div>
            </div>
            <div class="status-item">
                <h3>Last Updated</h3>
                <div class="value" id="lastUpdated">-</div>
            </div>
        </div>

        <div class="scales-grid">
            <div class="scale-column">
                <div class="scale-header scale-1">🍎 Scale 1</div>
                <div class="latest-reading" id="scale1Latest">
                    <div class="loading">
                        <div class="spinner"></div>
                        Loading...
                    </div>
                </div>
                <div class="logs-container">
                    <table class="logs-table">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Item</th>
                                <th>Amount</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id="scale1Logs">
                            <tr><td colspan="4" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="scale-column">
                <div class="scale-header scale-2">🥕 Scale 2</div>
                <div class="latest-reading" id="scale2Latest">
                    <div class="loading">
                        <div class="spinner"></div>
                        Loading...
                    </div>
                </div>
                <div class="logs-container">
                    <table class="logs-table">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Item</th>
                                <th>Amount</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id="scale2Logs">
                            <tr><td colspan="4" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="scale-column">
                <div class="scale-header scale-3">🧄 Scale 3</div>
                <div class="latest-reading" id="scale3Latest">
                    <div class="loading">
                        <div class="spinner"></div>
                        Loading...
                    </div>
                </div>
                <div class="logs-container">
                    <table class="logs-table">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Item</th>
                                <th>Amount</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id="scale3Logs">
                            <tr><td colspan="4" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="scale-column">
                <div class="scale-header scale-4">🥒 Scale 4</div>
                <div class="latest-reading" id="scale4Latest">
                    <div class="loading">
                        <div class="spinner"></div>
                        Loading...
                    </div>
                </div>
                <div class="logs-container">
                    <table class="logs-table">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Item</th>
                                <th>Amount</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id="scale4Logs">
                            <tr><td colspan="4" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <script>
        // Full capacities for each scale (set manually)
        const scaleCapacities = {
            "scale_1": 5000,  // full capacity in grams
            "scale_2": 2000,
            "scale_3": 3000,
            "scale_4": 6200
        };

    
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
            return date.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        }

        function formatAmount(amount, scaleNum) {
            if (!amount) return "0%";
        
            const fullAmount = scaleCapacities["scale_" + scaleNum] || 1;
            const percentage = (amount / fullAmount) * 100;
        
            return Math.round(percentage) + "%";
        }



        function getActionBadge(action) {
            const className = `action-${action.toLowerCase()}`;
            return `<span class="action-badge ${className}">${action}</span>`;
        }

        function updateLatestReading(scaleNum, record) {
            const container = document.getElementById(`scale${scaleNum}Latest`);
        
            if (!record || record.action !== "Heartbeat") {
                container.innerHTML = '<div class="no-data">No heartbeat data</div>';
                return;
            }
        
            const newHTML = `
                <div class="reading-ingredient">${record.ingredient}</div>
                <div class="reading-amount">${formatAmount(record.amount, scaleNum)}</div>
                <div class="reading-details">
                    <div class="reading-detail">
                        <div class="label">Location</div>
                        <div class="value">${record.location}</div>
                    </div>
                    <div class="reading-detail">
                        <div class="label">Action</div>
                        <div class="value">${getActionBadge(record.action)}</div>
                    </div>
                </div>
            `;
        
            if (container.innerHTML !== newHTML) {
                container.innerHTML = newHTML;
        
                container.classList.remove("flash-update");
                void container.offsetWidth; // restart animation
                container.classList.add("flash-update");
            }
        }




        function updateScaleLogs(scaleNum, records) {
            const tbody = document.getElementById(`scale${scaleNum}Logs`);
        
            const heartbeatRecords = (records || []).filter(r => r.action === "Heartbeat");
        
            if (heartbeatRecords.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" class="loading">No heartbeat activity</td></tr>';
                return;
            }
        
            tbody.innerHTML = heartbeatRecords.slice(0, 15).map(record => `
                <tr>
                    <td class="timestamp">${formatTimestamp(record.timestamp)}</td>
                    <td class="ingredient-name">${record.ingredient}</td>
                    <td class="amount-value">${formatAmount(record.amount, scaleNum)}</td>
                    <td>${getActionBadge(record.action)}</td>
                </tr>
            `).join('');
        }


        function loadScalesData() {
            // Load latest readings for each scale
            fetch('/api/latest_scales')
                .then(response => response.json())
                .then(data => {
                    updateConnectionStatus('connected');

                    let activeScales = 0;
                    for (let i = 1; i <= 4; i++) {
                        const scaleData = data.latest[`scale_${i}`];
                        updateLatestReading(i, scaleData);
                        if (scaleData) activeScales++;
                    }

                    document.getElementById('activeScales').textContent = activeScales;
                    document.getElementById('lastUpdated').textContent = formatTimestamp(data.timestamp);
                })
                .catch(error => {
                    console.error('Error loading latest data:', error);
                    updateConnectionStatus('disconnected');
                });

            // Load recent logs for each scale
            fetch('/api/scales?limit=20')
                .then(response => response.json())
                .then(data => {
                    let totalRecords = 0;

                    for (let i = 1; i <= 4; i++) {
                        const records = data.scales[`scale_${i}`] || [];
                        updateScaleLogs(i, records);
                        totalRecords += records.length;
                    }
                })
                .catch(error => {
                    console.error('Error loading scales data:', error);
                });
        }

        // Load initial data
        loadScalesData();

        // Refresh data every 2 seconds
        setInterval(loadScalesData, 2000);

        // Update timestamp every second for connected status
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
        logger.info(f"Starting Multi-Scale Libra Log Server on {self.host}:{self.port}")
        self.monitor.start_monitoring()

        try:
            self.app.run(host=self.host, port=self.port, debug=False, threaded=True)
        except KeyboardInterrupt:
            logger.info("Shutting down server...")
        finally:
            self.monitor.stop_monitoring()


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Multi-Scale Libra Log Database Monitor Server')
    parser.add_argument('--db', default='libra_logs.db', help='Path to SQLite database file')
    parser.add_argument('--port', type=int, default=5000, help='Port to serve on')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--poll-interval', type=float, default=1.0, help='Database polling interval in seconds')

    args = parser.parse_args()

    server = LibraLogServer(args.db, args.port, args.host)
    server.monitor.poll_interval = args.poll_interval

    logger.info("=== Multi-Scale Libra Log Monitor Server ===")
    logger.info(f"Database: {args.db}")
    logger.info(f"Server: http://{args.host}:{args.port}")
    logger.info(f"Poll interval: {args.poll_interval}s")
    logger.info("Available endpoints:")
    logger.info(f"  GET / - Multi-scale web dashboard (iPad optimized)")
    logger.info(f"  GET /health - Server health check")
    logger.info(f"  GET /api/recent?limit=N - Get recent records (default: 100)")
    logger.info(f"  GET /api/scales?limit=N - Get records grouped by scale (default: 20)")
    logger.info(f"  GET /api/latest_scales - Get latest record for each scale")
    logger.info(f"  GET /api/stream - Server-sent events stream (real-time)")

    try:
        server.start()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")


if __name__ == '__main__':
    main()