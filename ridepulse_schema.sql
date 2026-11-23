CREATE DATABASE IF NOT EXISTS ncr_rides_db;
USE ncr_rides_db;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS ride_bookings;
DROP TABLE IF EXISTS locations;
DROP TABLE IF EXISTS vehicle_types;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE vehicle_types (
    vehicle_type_id INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_name VARCHAR(50) NOT NULL UNIQUE,
    median_completed_fare DECIMAL(10, 2) DEFAULT 415.00
);

CREATE TABLE locations (
    location_id INT AUTO_INCREMENT PRIMARY KEY,
    location_name VARCHAR(100) NOT NULL UNIQUE
);

CREATE TABLE ride_bookings (
    record_id INT AUTO_INCREMENT PRIMARY KEY,
    booking_id VARCHAR(30) NOT NULL,
    customer_id VARCHAR(30) NOT NULL,
    ride_date DATE NOT NULL,
    ride_time TIME NOT NULL,
    ride_hour TINYINT NOT NULL,
    booking_status VARCHAR(40) NOT NULL,
    vehicle_type_id INT NOT NULL,
    pickup_location_id INT NOT NULL,
    drop_location_id INT NOT NULL,
    avg_vtat DECIMAL(6, 2) NULL,
    avg_ctat DECIMAL(6, 2) NULL,
    customer_cancel_reason VARCHAR(120) NULL,
    driver_cancel_reason VARCHAR(120) NULL,
    incomplete_reason VARCHAR(120) NULL,
    booking_value DECIMAL(10, 2) NULL,
    ride_distance DECIMAL(8, 2) NULL,
    driver_rating DECIMAL(3, 2) NULL,
    customer_rating DECIMAL(3, 2) NULL,
    payment_method VARCHAR(30) NULL,
    INDEX idx_status (booking_status),
    INDEX idx_hour (ride_hour),
    INDEX idx_vehicle (vehicle_type_id),
    INDEX idx_pickup (pickup_location_id),
    FOREIGN KEY (vehicle_type_id) REFERENCES vehicle_types(vehicle_type_id),
    FOREIGN KEY (pickup_location_id) REFERENCES locations(location_id),
    FOREIGN KEY (drop_location_id) REFERENCES locations(location_id)
);

CREATE OR REPLACE VIEW vw_vehicle_funnel_metrics AS
SELECT 
    v.vehicle_name AS vehicle_type,
    COUNT(*) AS total_bookings,
    SUM(CASE WHEN b.booking_status = 'Completed' THEN 1 ELSE 0 END) AS completed_rides,
    SUM(CASE WHEN b.booking_status = 'Cancelled by Driver' THEN 1 ELSE 0 END) AS driver_cancelled,
    SUM(CASE WHEN b.booking_status = 'Cancelled by Customer' THEN 1 ELSE 0 END) AS customer_cancelled,
    SUM(CASE WHEN b.booking_status = 'No Driver Found' THEN 1 ELSE 0 END) AS no_driver_found,
    SUM(CASE WHEN b.booking_status = 'Incomplete' THEN 1 ELSE 0 END) AS incomplete_rides,
    ROUND(SUM(CASE WHEN b.booking_status = 'Completed' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS completion_rate_pct,
    ROUND(SUM(CASE WHEN b.booking_status = 'Completed' THEN COALESCE(b.booking_value, 0) ELSE 0 END), 2) AS realized_gmv,
    ROUND(SUM(CASE WHEN b.booking_status != 'Completed' THEN COALESCE(b.booking_value, v.median_completed_fare) ELSE 0 END), 2) AS estimated_lost_gmv
FROM ride_bookings b
JOIN vehicle_types v ON b.vehicle_type_id = v.vehicle_type_id
GROUP BY v.vehicle_name;

CREATE OR REPLACE VIEW vw_location_cancellation_rank AS
SELECT 
    l.location_name AS pickup_location,
    COUNT(*) AS total_requests,
    SUM(CASE WHEN b.booking_status != 'Completed' THEN 1 ELSE 0 END) AS failed_requests,
    ROUND(SUM(CASE WHEN b.booking_status != 'Completed' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS failure_rate_pct,
    ROUND(AVG(b.avg_vtat), 2) AS avg_wait_mins,
    RANK() OVER (ORDER BY SUM(CASE WHEN b.booking_status != 'Completed' THEN 1 ELSE 0 END) DESC) AS failure_volume_rank
FROM ride_bookings b
JOIN locations l ON b.pickup_location_id = l.location_id
GROUP BY l.location_name;

DELIMITER //
CREATE PROCEDURE GetCancellationBreakdown(IN p_vehicle_type VARCHAR(50))
BEGIN
    SELECT 
        b.booking_status,
        COALESCE(b.driver_cancel_reason, b.customer_cancel_reason, b.incomplete_reason, 'No Driver Available') AS root_cause,
        COUNT(*) AS incident_count,
        ROUND(AVG(b.avg_vtat), 2) AS avg_vtat_mins,
        ROUND(SUM(COALESCE(b.booking_value, v.median_completed_fare)), 2) AS estimated_revenue_loss
    FROM ride_bookings b
    JOIN vehicle_types v ON b.vehicle_type_id = v.vehicle_type_id
    WHERE b.booking_status != 'Completed'
      AND (p_vehicle_type = 'ALL' OR v.vehicle_name = p_vehicle_type)
    GROUP BY b.booking_status, root_cause
    ORDER BY incident_count DESC;
END //
DELIMITER ;