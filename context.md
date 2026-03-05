

## Schema

Dataset: thelook-459020.thelook

Available Tables:
1. distribution_centers - Distribution/fulfillment centers
2. events - User web events and activities 
3. inventory_items - Product inventory tracking
4. order_items - Individual items within orders
5. orders - Customer orders
6. products - Product catalog
7. sales_people - Sales representatives (also used for access control)
8. users - Customer/user data

Table Details:
- distribution_centers: id, name, latitude, longitude, distribution_center_geom
- events: id, user_id, sequence_number, session_id, created_at, ip_address, city, state, postal_code, browser, traffic_source, uri, event_type
- inventory_items: id, product_id, cost, created_at, sold_at, distribution_center_id
- order_items: id, order_id, user_id, product_id, inventory_item_id, created_at, shipped_at, delivered_at, returned_at, sale_price
- orders: order_id, user_id, status, gender, created_at, returned_at, shipped_at, delivered_at, num_of_item
- products: id, cost, category, name, brand, retail_price, department, sku, distribution_center_id
- sales_people: sales_person_id, sales_person_name, sales_region, sales_territory, sales_manager
- users: id, first_name, last_name, email, age, gender, state, street_address, postal_code, city, country, latitude, longitude, traffic_source, created_at

## Sql Examples

Sales Analysis Queries:

1. Total Sales Summary:
SELECT 
  ROUND(SUM(oi.sale_price), 2) as total_sales,
  COUNT(DISTINCT oi.order_id) as total_orders,
  COUNT(oi.id) as total_items_sold,
  ROUND(AVG(oi.sale_price), 2) as avg_item_price
FROM `thelook-459020.thelook.order_items` oi

2. Top Products by Revenue:
SELECT 
  p.name as product_name,
  p.brand,
  p.category,
  COUNT(oi.id) as units_sold,
  ROUND(SUM(oi.sale_price), 2) as total_revenue
FROM `thelook-459020.thelook.order_items` oi
JOIN `thelook-459020.thelook.products` p ON oi.product_id = p.id
GROUP BY p.id, p.name, p.brand, p.category
ORDER BY total_revenue DESC

3. Top Products by Units Sold:
[Same query with ORDER BY units_sold DESC]