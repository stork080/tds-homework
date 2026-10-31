"""Seed initial weeks and questions. Usage:
   python seed.py
   Requires DATABASE_URL env variable set."""
import os
from app import app, db, Week, Question

weeks_data = [
    {"name": "Week 1", "order": 1},
    {"name": "Week 2", "order": 2},
    {"name": "Week 3", "order": 3},
    {"name": "Week 4", "order": 4},
    {"name": "Week 5", "order": 5},
    {"name": "Week 6", "order": 6},
    {"name": "Week 7", "order": 7},
    {"name": "Week 8", "order": 8},
    {"name": "Week 9", "order": 9},
    {"name": "Project 1", "order": 10, "is_project": True},
    {"name": "Project 2", "order": 11, "is_project": True},
    {"name": "Project 3", "order": 12, "is_project": True},
]

with app.app_context():
    db.create_all()
    for w in weeks_data:
        if not Week.query.filter_by(name=w["name"]).first():
            db.session.add(Week(**w))
    db.session.commit()
    print("Seeded weeks.")
