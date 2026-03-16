"""Database connection config and shared constants."""

import psycopg2
from contextlib import contextmanager

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 5433,
    "dbname": "gymzillatribe_dev",
    "user": "app",
    "password": "phevasTAz7d2",
}

TOP_CUISINES = [
    "American", "Italian", "Mexican", "European", "Asian",
    "Indian", "British", "French", "Chinese", "Mediterranean",
    "Greek", "Canadian", "Japanese", "Australian", "Thai",
    "German", "Spanish", "Southern/Soul", "African", "Middle Eastern",
    "Caribbean", "Fusion", "Korean", "South American",
]

PROTEIN_TYPES = [
    "Chicken", "Pork", "Beef", "Fish/Seafood", "Eggs",
    "Legumes", "Turkey", "Tofu/Tempeh", "Lamb", "Game",
]

MEAL_TYPES = [
    "Breakfast", "Lunch", "Dinner", "Snack", "Dessert",
    "Main Course", "Side Dish", "Beverage", "Appetizer",
]

DAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


@contextmanager
def get_connection():
    """Get a database connection as a context manager."""
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        yield conn
    finally:
        conn.close()
