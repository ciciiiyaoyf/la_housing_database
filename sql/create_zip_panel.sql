-- Partial coverage is intentional: no ZIP is removed because one provider is absent.
CREATE OR REPLACE VIEW analytics.v_zip_monthly_monitor AS
SELECT *
FROM analytics.zip_monthly_panel
WHERE primary_county_fips = '06037'
ORDER BY postal_zip, date;
