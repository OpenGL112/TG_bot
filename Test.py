import sqlite3

async def get_available_slots(service: str, date: str):
    try:
        conn = sqlite3.connect("appointments.db")
        cursor = conn.cursor()

        query = """
        SELECT id, time
        FROM slots
        WHERE service = ? AND date = ? AND is_booked = 0
        """
        cursor.execute(query, (service, date))
        slots = cursor.fetchall()
        return slots
    except sqlite3.Error as e:
        print(f"Error when getting available slots: {e}")
        return []
    finally:
        conn.close()