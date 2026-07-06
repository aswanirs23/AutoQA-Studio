"""Data access layer (SQLite repositories).

Each module groups SQL for one aggregate (projects, features, test cases, etc.). Routers call
these functions inside ``async with get_db()`` transactions. Swap implementations for Postgres later.
"""
