import hashlib
import json
import zipfile
from copy import deepcopy
from datetime import datetime, timezone

import pandas as pd
import pytest

from src.benchmark.acs import (
    ACSFormatError,
    ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION,
    ACS_TABLE_VARIABLES,
    ACS_VARIABLE_MAP,
    CALIFORNIA_COUNTY_FIPS,
    acs_finance_archive_metadata,
    acs_five_year_period,
    archive_acs_finance_summary_files,
    archive_acs_finance_vintage,
    build_acs_request_url,
    build_acs_summary_geography_url,
    build_acs_summary_table_url,
    clean_acs_finance_vintage,
    expected_acs_header,
    fetch_acs_finance_payload,
)
from src.benchmark.finance import REQUIRED_BENCHMARK_METADATA_FIELDS


BASE_VALUES = {
    "B01003_001": (1_500_000, 5_000),
    "B25089_002": (1_000_000, 10_000),
    "B25081_001": (800, 35),
    "B25081_002": (500, 25),
    "B25082_001": (300_000_000, 3_000_000),
    "B25082_002": (240_000_000, 2_400_000),
    "B25082_003": (60_000_000, 600_000),
    "B25120_002": (40_000_000, 400_000),
    "B25120_003": (32_000_000, 320_000),
    "B25120_004": (8_000_000, 80_000),
}


def _responses():
    responses = {}
    for table_id, variable_ids in ACS_TABLE_VARIABLES.items():
        rows = [list(expected_acs_header(table_id))]
        for offset, county_fips in enumerate(CALIFORNIA_COUNTY_FIPS):
            row = [f"County {county_fips}, California"]
            for variable_id in variable_ids:
                estimate, moe = BASE_VALUES[variable_id]
                row.extend([str(estimate + offset), str(moe)])
            row.extend(["06", county_fips[-3:]])
            rows.append(row)
        responses[table_id] = rows
    return responses


class _Response:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self._body)


class _Session:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, *, params, timeout, headers):
        self.calls.append(
            {
                "url": url,
                "params": dict(params),
                "timeout": timeout,
                "headers": dict(headers),
            }
        )
        first_variable = params["get"].split(",")[1]
        table_id = first_variable.split("_")[0]
        return _Response(self.responses[table_id])


def _fetched_payload(responses=None):
    return fetch_acs_finance_payload(
        2024,
        session=_Session(responses or _responses()),
        retrieved_at=datetime(2026, 7, 25, 15, 30, tzinfo=timezone.utc),
        release_date="2026-01-29",
    )


def _summary_file_variables(variable_id):
    table_id, line = variable_id.rsplit("_", 1)
    return f"{table_id}_E{line}", f"{table_id}_M{line}"


def _summary_file_assets():
    geography_lines = [
        (
            "FILEID|STUSAB|SUMLEVEL|COMPONENT|STATE|COUNTY|"
            "GEO_ID|NAME"
        )
    ]
    for county_fips in CALIFORNIA_COUNTY_FIPS:
        geography_lines.append(
            "ACSSF|CA|050|00|06|"
            f"{county_fips[-3:]}|0500000US{county_fips}|"
            f"County {county_fips}, California"
        )
    assets = {
        build_acs_summary_geography_url(2024): (
            "\n".join(geography_lines) + "\n"
        ).encode("utf-8")
    }

    for table_id, variable_ids in ACS_TABLE_VARIABLES.items():
        header = ["GEO_ID"]
        for variable_id in variable_ids:
            header.extend(_summary_file_variables(variable_id))
        lines = ["|".join(header)]
        for offset, county_fips in enumerate(CALIFORNIA_COUNTY_FIPS):
            row = [f"0500000US{county_fips}"]
            for variable_id in variable_ids:
                estimate, moe = BASE_VALUES[variable_id]
                row.extend([str(estimate + offset), str(moe)])
            lines.append("|".join(row))
        assets[build_acs_summary_table_url(2024, table_id)] = (
            "\n".join(lines) + "\n"
        ).encode("utf-8")
    return assets


class _BulkResponse:
    def __init__(self, body):
        self.body = body
        self.closed = False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        del chunk_size
        for start in range(0, len(self.body), 17):
            yield self.body[start : start + 17]

    def close(self):
        self.closed = True


class _BulkSession:
    def __init__(self, assets):
        self.assets = assets
        self.calls = []

    def get(self, url, *, stream, timeout, headers):
        self.calls.append(
            {
                "url": url,
                "stream": stream,
                "timeout": timeout,
                "headers": dict(headers),
            }
        )
        return _BulkResponse(self.assets[url])


def test_acs_period_is_explicit_60_month_interval_with_midpoint():
    start, end, reference = acs_five_year_period(2024)
    assert start == pd.Timestamp("2020-01-01")
    assert end == pd.Timestamp("2024-12-31")
    assert reference == pd.Timestamp("2022-07-01")
    assert reference.month == 7
    assert reference != end


def test_fetch_uses_official_all_california_county_queries_and_exact_headers():
    session = _Session(_responses())
    payload = fetch_acs_finance_payload(
        2024,
        api_key="not-archived",
        session=session,
        retrieved_at=datetime(2026, 7, 25, 15, 30, tzinfo=timezone.utc),
        release_date="2026-01-29",
    )

    assert len(session.calls) == 5
    assert set(payload["responses"]) == set(ACS_TABLE_VARIABLES)
    assert payload["retrieved_at"] == "2026-07-25T15:30:00+00:00"
    assert payload["variable_map"] == ACS_VARIABLE_MAP
    assert payload["source_interval_months"] == 60
    for table_id, call in zip(ACS_TABLE_VARIABLES, session.calls):
        assert call["url"] == (
            "https://api.census.gov/data/2024/acs/acs5"
        )
        assert call["params"]["for"] == "county:*"
        assert call["params"]["in"] == "state:06"
        assert call["params"]["key"] == "not-archived"
        assert tuple(payload["responses"][table_id][0]) == (
            expected_acs_header(table_id)
        )
        assert "not-archived" not in payload["request_url"][table_id]
        assert payload["request_url"][table_id] == build_acs_request_url(
            2024, table_id
        )


def test_fetch_rejects_header_drift_before_archiving(tmp_path):
    responses = _responses()
    responses["B25081"][0][1] = "B25081_999E"
    session = _Session(responses)
    output = tmp_path / "acs_2024.json"

    with pytest.raises(ACSFormatError, match="header changed"):
        archive_acs_finance_vintage(
            2024,
            output,
            session=session,
            retrieved_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
            release_date="2026-01-29",
        )
    assert not output.exists()


def test_archive_is_composite_checksummed_and_never_overwritten(tmp_path):
    output = tmp_path / "acs_2024.json"
    metadata = archive_acs_finance_vintage(
        2024,
        output,
        session=_Session(_responses()),
        retrieved_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        release_date="2026-01-29",
    )
    archived = json.loads(output.read_text(encoding="utf-8"))

    assert archived["source_filename"] == "acs_2024.json"
    assert set(archived["request_url"]) == set(ACS_TABLE_VARIABLES)
    assert set(archived["responses"]) == set(ACS_TABLE_VARIABLES)
    assert len(metadata["checksum_sha256"]) == 64
    assert metadata["response_count"] == 5

    with pytest.raises(FileExistsError):
        archive_acs_finance_vintage(
            2024,
            output,
            session=_Session(_responses()),
            release_date="2026-01-29",
        )


def test_clean_outputs_every_county_variable_with_moe_and_benchmark_metadata():
    clean = clean_acs_finance_vintage(_fetched_payload())

    assert len(clean) == len(CALIFORNIA_COUNTY_FIPS) * len(ACS_VARIABLE_MAP)
    assert set(REQUIRED_BENCHMARK_METADATA_FIELDS).issubset(clean.columns)
    assert set(clean["county_fips"]) == set(CALIFORNIA_COUNTY_FIPS)
    assert clean["variable_name"].str.endswith("_benchmark").all()
    assert clean["month"].isna().all()
    assert clean["release_date"].eq(pd.Timestamp("2026-01-29")).all()
    assert clean["margin_of_error"].equals(clean["moe"])
    assert clean["source_vintage"].eq("2024").all()
    assert clean["source_period_start"].eq(pd.Timestamp("2020-01-01")).all()
    assert clean["source_period_end"].eq(pd.Timestamp("2024-12-31")).all()
    assert clean["reference_date"].eq(pd.Timestamp("2022-07-01")).all()
    assert clean["source_interval_months"].eq(60).all()
    assert clean["is_modeled"].eq(False).all()
    assert clean["is_nowcast"].eq(False).all()

    row = clean.loc[
        clean["county_fips"].eq("06001")
        & clean["source_variable"].eq("B25089_002")
    ].iloc[0]
    assert row["value"] == 1_000_000
    assert row["moe"] == 10_000
    assert row["low_estimate"] == 990_000
    assert row["central_estimate"] == 1_000_000
    assert row["high_estimate"] == 1_010_000
    assert row["quality_flag"] == "reported"


def test_clean_preserves_census_suppression_as_a_null_benchmark_row():
    payload = _fetched_payload()
    header = payload["responses"]["B25082"][0]
    estimate_index = header.index("B25082_002E")
    moe_index = header.index("B25082_002M")
    county_row = payload["responses"]["B25082"][1]
    county_row[estimate_index] = "-888888888"
    county_row[moe_index] = None

    clean = clean_acs_finance_vintage(payload)
    row = clean.loc[
        clean["county_fips"].eq("06001")
        & clean["source_variable"].eq("B25082_002")
    ].iloc[0]

    assert pd.isna(row["value"])
    assert pd.isna(row["moe"])
    assert pd.isna(row["low_estimate"])
    assert pd.isna(row["central_estimate"])
    assert pd.isna(row["high_estimate"])
    assert row["estimate_raw"] == "-888888888"
    assert row["estimate_status"] == "census_special_value_-888888888"
    assert row["moe_status"] == "null"
    assert row["quality_flag"] == "estimate_and_moe_unavailable"


def test_clean_rejects_incomplete_county_responses():
    payload = _fetched_payload()
    payload["responses"]["B25120"].pop()
    with pytest.raises(ACSFormatError, match="county coverage changed"):
        clean_acs_finance_vintage(payload)


def test_summary_file_archive_preserves_exact_provider_bytes_and_provenance(
    tmp_path,
):
    assets = _summary_file_assets()
    session = _BulkSession(assets)
    output = tmp_path / "acs_summary_2024.zip"

    archived = archive_acs_finance_summary_files(
        2024,
        output,
        session=session,
        retrieved_at=datetime(2026, 7, 26, 8, 15, tzinfo=timezone.utc),
        release_date="2026-01-29",
    )

    assert archived["schema_version"] == ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION
    assert archived["acquisition_method"] == (
        "official_table_based_summary_file"
    )
    assert len(session.calls) == 6
    assert all(call["stream"] is True for call in session.calls)
    assert len(archived["checksum_sha256"]) == 64

    with zipfile.ZipFile(output) as package:
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["schema_version"] == ACS_SUMMARY_ARCHIVE_SCHEMA_VERSION
        geography_asset = manifest["source_assets"]["geography_labels"]
        geography_url = build_acs_summary_geography_url(2024)
        assert package.read(geography_asset["archive_member"]) == assets[
            geography_url
        ]
        assert geography_asset["checksum_sha256"] == hashlib.sha256(
            assets[geography_url]
        ).hexdigest()

        for table_id in ACS_TABLE_VARIABLES:
            asset = manifest["source_assets"]["tables"][table_id]
            url = build_acs_summary_table_url(2024, table_id)
            assert asset["source_url"] == url
            assert package.read(asset["archive_member"]) == assets[url]
            assert asset["checksum_sha256"] == hashlib.sha256(
                assets[url]
            ).hexdigest()

    metadata = acs_finance_archive_metadata(output)
    assert metadata["release_date"] == "2026-01-29"
    assert metadata["acquisition_method"] == (
        "official_table_based_summary_file"
    )
    assert metadata["source_filename"] == output.name


def test_summary_file_clean_maps_bulk_fields_without_claiming_api_provenance(
    tmp_path,
):
    assets = _summary_file_assets()
    output = tmp_path / "acs_summary_2024.zip"
    archive_acs_finance_summary_files(
        2024,
        output,
        session=_BulkSession(assets),
        retrieved_at=datetime(2026, 7, 26, 8, 15, tzinfo=timezone.utc),
        release_date="2026-01-29",
    )

    clean = clean_acs_finance_vintage(output)

    assert len(clean) == len(CALIFORNIA_COUNTY_FIPS) * len(ACS_VARIABLE_MAP)
    assert set(clean["county_fips"]) == set(CALIFORNIA_COUNTY_FIPS)
    assert clean["acquisition_method"].eq(
        "official_table_based_summary_file"
    ).all()
    assert clean["archive_filename"].eq(output.name).all()
    assert clean["request_url"].str.contains(
        "/summary_file/2024/table-based-SF/data/5YRData/"
    ).all()
    assert not clean["request_url"].str.contains("api.census.gov").any()
    assert clean["notes"].str.contains("Table-Based Summary File").all()

    row = clean.loc[
        clean["county_fips"].eq("06001")
        & clean["source_variable"].eq("B25089_002")
    ].iloc[0]
    table_url = build_acs_summary_table_url(2024, "B25089")
    assert row["value"] == 1_000_000
    assert row["moe"] == 10_000
    assert row["source_filename"] == "acsdt5y2024-b25089.dat"
    assert row["checksum_sha256"] == hashlib.sha256(
        assets[table_url]
    ).hexdigest()
    assert row["geography_source_filename"] == "Geos20245YR.txt"
    assert row["estimate_variable"] == "B25089_E002"
    assert row["moe_variable"] == "B25089_M002"
    assert row["canonical_api_estimate_variable"] == "B25089_002E"
    assert row["canonical_api_moe_variable"] == "B25089_002M"
    assert row["estimate_raw"] == "1000000"
    assert row["moe_raw"] == "10000"


def test_summary_file_clean_rejects_embedded_member_checksum_drift(tmp_path):
    assets = _summary_file_assets()
    original = tmp_path / "acs_summary_2024.zip"
    archive_acs_finance_summary_files(
        2024,
        original,
        session=_BulkSession(assets),
        retrieved_at=datetime(2026, 7, 26, 8, 15, tzinfo=timezone.utc),
        release_date="2026-01-29",
    )
    tampered = tmp_path / "acs_summary_2024_tampered.zip"
    changed_member = "acsdt5y2024-b01003.dat"
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(
        tampered, "x"
    ) as target:
        for member in source.namelist():
            body = source.read(member)
            if member == changed_member:
                body = bytes([body[0] ^ 1]) + body[1:]
            target.writestr(member, body)

    with pytest.raises(ACSFormatError, match="checksum differs"):
        clean_acs_finance_vintage(tampered)


def test_summary_file_archive_rejects_incomplete_counties_and_is_not_retained(
    tmp_path,
):
    assets = _summary_file_assets()
    table_url = build_acs_summary_table_url(2024, "B25120")
    lines = assets[table_url].decode("utf-8").splitlines()
    assets[table_url] = ("\n".join(lines[:-1]) + "\n").encode("utf-8")
    output = tmp_path / "acs_summary_incomplete.zip"

    with pytest.raises(ACSFormatError, match="county coverage changed"):
        archive_acs_finance_summary_files(
            2024,
            output,
            session=_BulkSession(assets),
            retrieved_at=datetime(2026, 7, 26, 8, 15, tzinfo=timezone.utc),
            release_date="2026-01-29",
        )
    assert not output.exists()
