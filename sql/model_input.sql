-- Current-snapshot LA County rent levels; preserve missing values and source units.
-- This selection does not reconstruct historical information vintages.
SELECT date, county_fips, zori
FROM analytics.county_monthly_panel
WHERE county_fips = '06037'
  AND date >= DATE '2020-01-01'
ORDER BY date;
