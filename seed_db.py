import os
from db import Base, engine, SessionLocal, User, ConstructionUpdate
from sqlalchemy.orm import Session

SEED_USERS = {
    "user_001": {
        "id": "user_001",
        "name": "Aditya",
        "phone": "+917903604458",
        "language": "en",
        "project": "Riverwood Estate - Tower A",
        "unit": "3BHK - 12th Floor, Unit 1204",
        "booking_date": "2025-03-15",
        "payment_status": "On Track",
    }
}

SEED_UPDATES = {
    "Riverwood Estate - Tower A": {
        "update_id": "towerA_phase3_v1",
        "current_phase": "Phase 3 - Interior Finishing",
        "completion_percentage": 72,
        "recent_milestone": "Plumbing and electrical work completed on floors 1-15",
        "next_milestone": "Flooring and wall painting begins next week",
        "expected_completion": "December 2026",
        "site_visit_available": True,
        "site_visit_timings": "Saturday and Sunday, 10:00 AM to 5:00 PM",
    }
}

def seed():
    Base.metadata.create_all(bind=engine)
    db: Session = SessionLocal()
    try:
        for uid, u in SEED_USERS.items():
            if not db.query(User).filter(User.id == u["id"]).first():
                db.add(User(**u))
        for project_name, upd in SEED_UPDATES.items():
            if not db.query(ConstructionUpdate).filter(ConstructionUpdate.project == project_name).first():
                db.add(ConstructionUpdate(project=project_name, **upd))
        db.commit()
        print("Database seeded successfully.")
    finally:
        db.close()

if __name__ == "__main__":
    seed()