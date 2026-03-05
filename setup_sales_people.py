"""Create sales_people table with sample data"""
import os
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "service_account.json"

client = bigquery.Client()
dataset_id = "thelook-459020.thelook"

# Create the sales_people table
create_table_sql = """
CREATE TABLE IF NOT EXISTS `thelook-459020.thelook.sales_people` (
    id INT64,
    username STRING,
    name STRING,
    geography STRING,
    department STRING
)
"""

# Insert sample data
insert_sql = """
INSERT INTO `thelook-459020.thelook.sales_people` (id, username, name, geography, department)
VALUES
    (1, 'john_us', 'John Smith', 'US', 'Men'),
    (2, 'sarah_us', 'Sarah Johnson', 'US', 'Women'),
    (3, 'mike_intl', 'Mike Chen', 'International', 'Men'),
    (4, 'emma_intl', 'Emma Wilson', 'International', 'Women'),
    (5, 'alex_us_all', 'Alex Brown', 'US', 'All'),
    (6, 'lisa_intl_all', 'Lisa Garcia', 'International', 'All')
"""

print("Creating sales_people table...")
client.query(create_table_sql).result()
print("Table created!")

print("Inserting sample data...")
client.query(insert_sql).result()
print("Data inserted!")

# Verify
verify_sql = "SELECT * FROM `thelook-459020.thelook.sales_people`"
result = client.query(verify_sql).result()
print("\nSales people in system:")
for row in result:
    print(f"  {row.username}: {row.name} | {row.geography} | {row.department}")
