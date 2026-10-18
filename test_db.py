
from database import get_db_connection


connection = None

try:
    connection = get_db_connection()

    cursor = connection.cursor()
    cursor.execute("SHOW TABLES")

    print("Connected to MySQL successfully!")
    print("Tables in notewise_db:")

    for table in cursor.fetchall():
        print(table[0])

    cursor.close()

except Exception as error:
    print("Database connection failed.")
    print("Error:", error)

finally:
    if connection is not None and connection.is_connected():
        connection.close()
