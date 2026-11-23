import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

connection_url = URL.create(
    drivername="mysql+mysqlconnector",
    username="your_user",
    password="your_password",
    host="localhost",
    port=3306,
    database="ncr_rides_db",
)
engine = create_engine(connection_url)

df = pd.read_csv("ncr_ride_bookings.csv")

df["booking_id"] = df["Booking ID"].str.replace('"', "", regex=False).str.strip()
df["customer_id"] = df["Customer ID"].str.replace('"', "", regex=False).str.strip()
df["ride_date"] = pd.to_datetime(df["Date"]).dt.date
df["ride_time"] = df["Time"]
df["ride_hour"] = pd.to_datetime(df["Time"], format="%H:%M:%S").dt.hour

completed_medians = (
    df[df["Booking Status"] == "Completed"]
    .groupby("Vehicle Type")["Booking Value"]
    .median()
    .reset_index()
    .rename(columns={"Vehicle Type": "vehicle_name", "Booking Value": "median_completed_fare"})
)

with engine.begin() as conn:
    conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
    conn.execute(text("TRUNCATE TABLE ride_bookings;"))
    conn.execute(text("TRUNCATE TABLE locations;"))
    conn.execute(text("TRUNCATE TABLE vehicle_types;"))
    conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))

completed_medians.to_sql("vehicle_types", con=engine, if_exists="append", index=False)

all_locations = pd.DataFrame({
    "location_name": sorted(set(df["Pickup Location"]).union(set(df["Drop Location"])))
})
all_locations.to_sql("locations", con=engine, if_exists="append", index=False)

veh_map = pd.read_sql("SELECT vehicle_type_id, vehicle_name FROM vehicle_types", engine).set_index("vehicle_name")["vehicle_type_id"].to_dict()
loc_map = pd.read_sql("SELECT location_id, location_name FROM locations", engine).set_index("location_name")["location_id"].to_dict()

fact_df = pd.DataFrame({
    "booking_id": df["booking_id"],
    "customer_id": df["customer_id"],
    "ride_date": df["ride_date"],
    "ride_time": df["ride_time"],
    "ride_hour": df["ride_hour"],
    "booking_status": df["Booking Status"],
    "vehicle_type_id": df["Vehicle Type"].map(veh_map),
    "pickup_location_id": df["Pickup Location"].map(loc_map),
    "drop_location_id": df["Drop Location"].map(loc_map),
    "avg_vtat": df["Avg VTAT"],
    "avg_ctat": df["Avg CTAT"],
    "customer_cancel_reason": df["Reason for cancelling by Customer"],
    "driver_cancel_reason": df["Driver Cancellation Reason"],
    "incomplete_reason": df["Incomplete Rides Reason"],
    "booking_value": df["Booking Value"],
    "ride_distance": df["Ride Distance"],
    "driver_rating": df["Driver Ratings"],
    "customer_rating": df["Customer Rating"],
    "payment_method": df["Payment Method"],
})

fact_df.to_sql("ride_bookings", con=engine, if_exists="append", index=False, chunksize=10000)
print(f"Successfully loaded {len(fact_df):,} records into local ncr_rides_db.ride_bookings!")
