-- The production panel is materialized from Parquet by src/harmonization/build.py.
-- This view exposes the stable modeling columns from the integrated table.
CREATE OR REPLACE VIEW analytics.v_county_monthly_model AS
SELECT *
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
ORDER BY date;
