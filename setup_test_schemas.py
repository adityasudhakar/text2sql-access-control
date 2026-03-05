"""
Create 3 test datasets in thelook-459020 with different schema patterns:
1. test_partitioned - separate tables for sales and HR data
2. test_columns - single table with sales and salary columns
3. test_flat - comingled flat file with data_type tag
"""
import os
from google.cloud import bigquery

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "service_account.json"

client = bigquery.Client()
PROJECT = "thelook-459020"

# Sample salespeople data (same across all schemas)
SALESPEOPLE = [
    (1, "john_us", "John Smith", "US", "Men"),
    (2, "sarah_us", "Sarah Johnson", "US", "Women"),
    (3, "mike_intl", "Mike Chen", "International", "Men"),
    (4, "emma_intl", "Emma Wilson", "International", "Women"),
    (5, "alex_us_all", "Alex Brown", "US", "All"),
    (6, "lisa_intl_all", "Lisa Garcia", "International", "All"),
]

# Sample sales data
SALES_DATA = [
    (1, 1, "2024-01", 50000),
    (2, 1, "2024-02", 55000),
    (3, 2, "2024-01", 45000),
    (4, 2, "2024-02", 48000),
    (5, 3, "2024-01", 60000),
    (6, 3, "2024-02", 62000),
    (7, 4, "2024-01", 42000),
    (8, 4, "2024-02", 44000),
    (9, 5, "2024-01", 70000),
    (10, 5, "2024-02", 75000),
    (11, 6, "2024-01", 65000),
    (12, 6, "2024-02", 68000),
]

# Sample HR/salary data (sensitive!)
HR_DATA = [
    (1, 1, 85000, "Senior"),
    (2, 2, 82000, "Senior"),
    (3, 3, 90000, "Lead"),
    (4, 4, 78000, "Mid"),
    (5, 5, 95000, "Lead"),
    (6, 6, 88000, "Senior"),
]


def create_dataset(dataset_id):
    full_id = f"{PROJECT}.{dataset_id}"
    dataset = bigquery.Dataset(full_id)
    dataset.location = "US"
    try:
        client.create_dataset(dataset, exists_ok=True)
        print(f"Created dataset: {full_id}")
    except Exception as e:
        print(f"Dataset {full_id}: {e}")


def run_query(sql):
    try:
        job = client.query(sql)
        job.result()
        return True
    except Exception as e:
        print(f"Error: {e}")
        return False


def setup_partitioned():
    """
    Scenario 1: Partitioned tables
    - salespeople table
    - sales_data table (FK to salespeople)
    - hr_data table (FK to salespeople) - sensitive!
    """
    print("\n" + "="*50)
    print("Setting up test_partitioned")
    print("="*50)

    dataset = "test_partitioned"
    create_dataset(dataset)

    # Salespeople table
    run_query(f"""
    CREATE OR REPLACE TABLE `{PROJECT}.{dataset}.salespeople` (
        id INT64,
        username STRING,
        name STRING,
        geography STRING,
        department STRING
    )
    """)

    values = ", ".join([f"({id}, '{u}', '{n}', '{g}', '{d}')" for id, u, n, g, d in SALESPEOPLE])
    run_query(f"INSERT INTO `{PROJECT}.{dataset}.salespeople` VALUES {values}")
    print("  Created salespeople table")

    # Sales data table
    run_query(f"""
    CREATE OR REPLACE TABLE `{PROJECT}.{dataset}.sales_data` (
        id INT64,
        salesperson_id INT64,
        month STRING,
        amount FLOAT64
    )
    """)

    values = ", ".join([f"({id}, {sp_id}, '{m}', {amt})" for id, sp_id, m, amt in SALES_DATA])
    run_query(f"INSERT INTO `{PROJECT}.{dataset}.sales_data` VALUES {values}")
    print("  Created sales_data table")

    # HR data table (sensitive - salaries)
    run_query(f"""
    CREATE OR REPLACE TABLE `{PROJECT}.{dataset}.hr_data` (
        id INT64,
        salesperson_id INT64,
        salary FLOAT64,
        level STRING
    )
    """)

    values = ", ".join([f"({id}, {sp_id}, {sal}, '{lvl}')" for id, sp_id, sal, lvl in HR_DATA])
    run_query(f"INSERT INTO `{PROJECT}.{dataset}.hr_data` VALUES {values}")
    print("  Created hr_data table")


def setup_columns():
    """
    Scenario 2: Multi-column table
    - salespeople table
    - employee_metrics table with both sales and salary columns
    """
    print("\n" + "="*50)
    print("Setting up test_columns")
    print("="*50)

    dataset = "test_columns"
    create_dataset(dataset)

    # Salespeople table
    run_query(f"""
    CREATE OR REPLACE TABLE `{PROJECT}.{dataset}.salespeople` (
        id INT64,
        username STRING,
        name STRING,
        geography STRING,
        department STRING
    )
    """)

    values = ", ".join([f"({id}, '{u}', '{n}', '{g}', '{d}')" for id, u, n, g, d in SALESPEOPLE])
    run_query(f"INSERT INTO `{PROJECT}.{dataset}.salespeople` VALUES {values}")
    print("  Created salespeople table")

    # Combined metrics table
    run_query(f"""
    CREATE OR REPLACE TABLE `{PROJECT}.{dataset}.employee_metrics` (
        id INT64,
        salesperson_id INT64,
        month STRING,
        sales_amount FLOAT64,
        salary FLOAT64,
        level STRING
    )
    """)

    combined_data = []
    for id, sp_id, month, amount in SALES_DATA:
        hr_row = next((h for h in HR_DATA if h[1] == sp_id), None)
        salary = hr_row[2] if hr_row else 0
        level = hr_row[3] if hr_row else "Unknown"
        combined_data.append((id, sp_id, month, amount, salary, level))

    values = ", ".join([f"({id}, {sp_id}, '{m}', {amt}, {sal}, '{lvl}')"
                        for id, sp_id, m, amt, sal, lvl in combined_data])
    run_query(f"INSERT INTO `{PROJECT}.{dataset}.employee_metrics` VALUES {values}")
    print("  Created employee_metrics table")


def setup_flat():
    """
    Scenario 3: Comingled flat file
    - salespeople table
    - all_data table with data_type column (sales vs salary)
    """
    print("\n" + "="*50)
    print("Setting up test_flat")
    print("="*50)

    dataset = "test_flat"
    create_dataset(dataset)

    # Salespeople table
    run_query(f"""
    CREATE OR REPLACE TABLE `{PROJECT}.{dataset}.salespeople` (
        id INT64,
        username STRING,
        name STRING,
        geography STRING,
        department STRING
    )
    """)

    values = ", ".join([f"({id}, '{u}', '{n}', '{g}', '{d}')" for id, u, n, g, d in SALESPEOPLE])
    run_query(f"INSERT INTO `{PROJECT}.{dataset}.salespeople` VALUES {values}")
    print("  Created salespeople table")

    # Flat file with all data
    run_query(f"""
    CREATE OR REPLACE TABLE `{PROJECT}.{dataset}.all_data` (
        id INT64,
        salesperson_id INT64,
        data_type STRING,
        period STRING,
        value FLOAT64,
        metadata STRING
    )
    """)

    flat_data = []
    row_id = 1
    for _, sp_id, month, amount in SALES_DATA:
        flat_data.append((row_id, sp_id, "sales", month, amount, "monthly_sales"))
        row_id += 1

    for _, sp_id, salary, level in HR_DATA:
        flat_data.append((row_id, sp_id, "salary", "2024", salary, level))
        row_id += 1

    values = ", ".join([f"({id}, {sp_id}, '{dtype}', '{period}', {val}, '{meta}')"
                        for id, sp_id, dtype, period, val, meta in flat_data])
    run_query(f"INSERT INTO `{PROJECT}.{dataset}.all_data` VALUES {values}")
    print("  Created all_data table")


def verify_setup():
    print("\n" + "="*50)
    print("Verifying setup")
    print("="*50)

    for dataset in ["test_partitioned", "test_columns", "test_flat"]:
        print(f"\n{dataset}:")
        tables = list(client.list_tables(f"{PROJECT}.{dataset}"))
        for table in tables:
            full_id = f"{PROJECT}.{dataset}.{table.table_id}"
            count_result = client.query(f"SELECT COUNT(*) as cnt FROM `{full_id}`").result()
            count = list(count_result)[0].cnt
            print(f"  {table.table_id}: {count} rows")


if __name__ == "__main__":
    setup_partitioned()
    setup_columns()
    setup_flat()
    verify_setup()
    print("\nAll test schemas created!")
