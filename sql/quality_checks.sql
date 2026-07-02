-- Structural checks mirrored by src/validation/qa.py.
SELECT observation_date, geo_id, variable_id, source_vintage, count(*) AS duplicate_count
FROM analytics.fact_housing_observation
GROUP BY ALL
HAVING count(*) > 1;

SELECT *
FROM analytics.fact_housing_observation
WHERE geo_id IS NULL OR variable_id IS NULL OR observation_date IS NULL;
