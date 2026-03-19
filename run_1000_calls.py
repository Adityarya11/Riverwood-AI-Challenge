from worker import dispatch_call_task
from db import SessionLocal, User

def simulate_1000_calls():
    db = SessionLocal()
    users = db.query(User).all()
    db.close()

    if not users:
        print("Initialization failed: Database contains no user records. Seed required.")
        return

    print("Dispatching bulk tasks to concurrency queue...")
    
    total_calls = 1000
    for i in range(total_calls):
        # Round-robin mapping for simulation purposes
        user = users[i % len(users)]
        
        # Asynchronously deploy task onto Redis message broker
        dispatch_call_task.delay(user.id)
        
        if i > 0 and i % 100 == 0:
            print(f"Propagated {i} tasks into queue...")
            
    print("Task dispatch complete. Awaiting downstream worker execution parameters.")

if __name__ == '__main__':
    simulate_1000_calls()
