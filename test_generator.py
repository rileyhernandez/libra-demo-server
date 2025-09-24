#!/usr/bin/env python3
"""
Libra Logs Test Data Generator

Generates realistic test data for the libra_logs database to test the monitoring server.
Can generate single entries, continuous streams, or batch data.
"""

import sqlite3
import time
import random
import argparse
from datetime import datetime, timedelta
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class LibraTestDataGenerator:
    def __init__(self, db_path):
        self.db_path = db_path
        self.running = False

        # Sample data for realistic test entries
        self.models = ["LibraV0", "LibraV1", "LibraV2"]
        self.scale_numbers = ['716710-0-0', '716710-1', '716710-2', '716710-3']
        self.actions = ["Heartbeat", "Refilled", "Served"]
        self.locations = [
            "Kitchen Counter", "Prep Station A", "Prep Station B",
            "Storage Room", "Loading Dock", "Quality Lab",
            "Production Line 1", "Production Line 2"
        ]
        self.ingredients = [
            "Flour", "Sugar", "Salt", "Butter", "Eggs", "Milk",
            "Vanilla Extract", "Baking Powder", "Cocoa Powder", "Yeast",
            "Olive Oil", "Tomatoes", "Onions", "Garlic", "Cheese",
            "Chicken Breast", "Ground Beef", "Rice", "Pasta", "Herbs"
        ]

        # Weight ranges for different ingredients (in grams)
        self.weight_ranges = {
            "Flour": (100, 5000),
            "Sugar": (50, 2000),
            "Salt": (5, 500),
            "Butter": (100, 1000),
            "Eggs": (50, 600),  # per egg ~50g
            "Milk": (200, 2000),
            "Vanilla Extract": (5, 100),
            "Baking Powder": (5, 200),
            "Cocoa Powder": (20, 500),
            "Yeast": (5, 100),
            "Olive Oil": (50, 1000),
            "Tomatoes": (100, 3000),
            "Onions": (100, 2000),
            "Garlic": (10, 200),
            "Cheese": (100, 2000),
            "Chicken Breast": (200, 5000),
            "Ground Beef": (250, 4000),
            "Rice": (200, 5000),
            "Pasta": (100, 3000),
            "Herbs": (5, 100)
        }

    def create_database(self):
        """Create the database and table if they don't exist"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                               CREATE TABLE IF NOT EXISTS libra_logs
                               (
                                   model
                                   TEXT
                                   NOT
                                   NULL,
                                   number
                                   TEXT
                                   NOT
                                   NULL,
                                   timestamp
                                   TEXT
                                   NOT
                                   NULL,
                                   action
                                   TEXT
                                   NOT
                                   NULL,
                                   amount
                                   NUMBER
                                   NOT
                                   NULL,
                                   location
                                   TEXT
                                   NOT
                                   NULL,
                                   ingredient
                                   TEXT
                                   NOT
                                   NULL,
                                   synced
                                   INTEGER
                                   NOT
                                   NULL
                                   DEFAULT
                                   0
                               )
                               """)
                conn.commit()
                logger.info(f"Database and table ready: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Error creating database: {e}")

    def generate_entry(self, timestamp=None, action=None, ingredient=None, location=None):
        """Generate a single realistic test entry"""
        if timestamp is None:
            timestamp = datetime.now().isoformat()

        if action is None:
            # Weight actions more towards Heartbeat for realistic data
            action = random.choices(
                self.actions,
                weights=[5, 80, 5]  # Starting: 5%, Heartbeat: 80%, Offline: 5%
            )[0]

        if ingredient is None:
            ingredient = random.choice(self.ingredients)

        if location is None:
            location = random.choice(self.locations)

        # Generate realistic weight based on ingredient
        min_weight, max_weight = self.weight_ranges.get(ingredient, (10, 1000))

        # Add some variation - occasionally zero for "Starting" or "Offline"
        if action in ["Starting", "Offline"]:
            amount = 0 if random.random() < 0.3 else random.uniform(min_weight, max_weight)
        else:
            # For heartbeat, simulate gradual changes
            amount = random.uniform(min_weight, max_weight)

        return {
            'model': random.choice(self.models),
            'number': random.choice(self.scale_numbers),
            'timestamp': timestamp,
            'action': action,
            'amount': round(amount, 2),
            'location': location,
            'ingredient': ingredient,
            'synced': random.choice([0, 1])  # Mix of synced and unsynced
        }

    def insert_entry(self, entry):
        """Insert a single entry into the database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                               INSERT INTO libra_logs
                               (model, number, timestamp, action, amount, location, ingredient, synced)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                               """, (
                                   entry['model'], entry['number'], entry['timestamp'],
                                   entry['action'], entry['amount'], entry['location'],
                                   entry['ingredient'], entry['synced']
                               ))
                conn.commit()
                logger.info(
                    f"Added: {entry['ingredient']} = {entry['amount']}g at {entry['location']} ({entry['action']})")
                return True
        except sqlite3.Error as e:
            logger.error(f"Error inserting entry: {e}")
            return False

    def generate_batch(self, count, start_time=None, time_spread_hours=1):
        """Generate a batch of entries spread over time"""
        if start_time is None:
            start_time = datetime.now() - timedelta(hours=time_spread_hours)

        entries = []
        for i in range(count):
            # Spread entries over the time range
            time_offset = timedelta(
                seconds=random.uniform(0, time_spread_hours * 3600)
            )
            entry_time = start_time + time_offset

            entry = self.generate_entry(timestamp=entry_time.isoformat())
            entries.append(entry)

        # Sort by timestamp
        entries.sort(key=lambda x: x['timestamp'])

        # Insert all entries
        for entry in entries:
            self.insert_entry(entry)

        logger.info(f"Generated {count} batch entries")

    def start_continuous_generation(self, interval=2.0, burst_chance=0.1):
        """Start continuous generation in a separate thread"""
        self.running = True

        def generate_loop():
            while self.running:
                try:
                    # Occasionally generate burst of entries (simulating busy periods)
                    if random.random() < burst_chance:
                        burst_size = random.randint(2, 5)
                        logger.info(f"Generating burst of {burst_size} entries")
                        for _ in range(burst_size):
                            entry = self.generate_entry()
                            self.insert_entry(entry)
                            time.sleep(0.2)  # Quick succession
                    else:
                        # Normal single entry
                        entry = self.generate_entry()
                        self.insert_entry(entry)

                    # Wait for next entry
                    actual_interval = interval + random.uniform(-0.5, 0.5)  # Add jitter
                    time.sleep(max(0.1, actual_interval))

                except Exception as e:
                    logger.error(f"Error in generation loop: {e}")
                    time.sleep(interval)

        self.generation_thread = threading.Thread(target=generate_loop, daemon=True)
        self.generation_thread.start()
        logger.info(f"Started continuous generation (interval: {interval}s)")

    def stop_continuous_generation(self):
        """Stop continuous generation"""
        self.running = False
        logger.info("Stopped continuous generation")

    def simulate_scale_scenario(self, scale_number, ingredient, location, duration_minutes=5):
        """Simulate a realistic scale scenario (e.g., measuring ingredient over time)"""
        logger.info(f"Starting scale scenario: {ingredient} at {location} for {duration_minutes} minutes")

        # Starting entry
        start_entry = self.generate_entry(
            action="Starting",
            ingredient=ingredient,
            location=location
        )
        start_entry['number'] = scale_number
        start_entry['amount'] = 0
        self.insert_entry(start_entry)

        # Simulate gradual weight increase
        min_weight, max_weight = self.weight_ranges.get(ingredient, (100, 1000))
        target_weight = random.uniform(min_weight, max_weight)

        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)

        while time.time() < end_time:
            elapsed = time.time() - start_time
            total_duration = duration_minutes * 60
            progress = elapsed / total_duration

            # Simulate realistic weight progression (not perfectly linear)
            current_weight = target_weight * progress
            current_weight += random.uniform(-target_weight * 0.05, target_weight * 0.05)  # Add noise
            current_weight = max(0, current_weight)  # Can't be negative

            heartbeat_entry = self.generate_entry(
                action="Heartbeat",
                ingredient=ingredient,
                location=location
            )
            heartbeat_entry['number'] = scale_number
            heartbeat_entry['amount'] = round(current_weight, 2)
            self.insert_entry(heartbeat_entry)

            time.sleep(random.uniform(1, 3))  # Vary heartbeat interval

        # Final offline entry
        offline_entry = self.generate_entry(
            action="Offline",
            ingredient=ingredient,
            location=location
        )
        offline_entry['number'] = scale_number
        offline_entry['amount'] = round(target_weight, 2)
        self.insert_entry(offline_entry)

        logger.info(f"Completed scale scenario: {ingredient} = {target_weight:.2f}g")


def main():
    parser = argparse.ArgumentParser(description='Generate test data for libra_logs database')
    parser.add_argument('--db', default='data.db', help='Database file path')
    parser.add_argument('--mode', choices=['single', 'batch', 'continuous', 'scenario'],
                        default='single', help='Generation mode')
    parser.add_argument('--count', type=int, default=1, help='Number of entries (for batch mode)')
    parser.add_argument('--interval', type=float, default=2.0, help='Interval in seconds (for continuous mode)')
    parser.add_argument('--duration', type=int, default=5, help='Duration in minutes (for scenario mode)')

    args = parser.parse_args()

    generator = LibraTestDataGenerator(args.db)
    generator.create_database()

    logger.info("=== Libra Test Data Generator ===")
    logger.info(f"Database: {args.db}")
    logger.info(f"Mode: {args.mode}")

    try:
        if args.mode == 'single':
            entry = generator.generate_entry()
            generator.insert_entry(entry)

        elif args.mode == 'batch':
            logger.info(f"Generating {args.count} entries...")
            generator.generate_batch(args.count)

        elif args.mode == 'continuous':
            logger.info(f"Starting continuous generation (interval: {args.interval}s)")
            logger.info("Press Ctrl+C to stop")
            generator.start_continuous_generation(args.interval)

            # Keep running until interrupted
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                generator.stop_continuous_generation()
                logger.info("Generation stopped")

        elif args.mode == 'scenario':
            # Run a realistic scale scenario
            scale_num = random.choice(generator.scale_numbers)
            ingredient = random.choice(generator.ingredients)
            location = random.choice(generator.locations)

            generator.simulate_scale_scenario(
                scale_num, ingredient, location, args.duration
            )

    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == '__main__':
    main()