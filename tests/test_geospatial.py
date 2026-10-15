from modules.geospatial import evaluate_geospatial_records, load_geojson, styled_geojson


def test_demo_geojson_includes_valid_overlapping_and_invalid_shapes():
    collection = load_geojson("data/demo/parcel_geometries.geojson")
    records = [
        {
            "id": feature["properties"]["record_id"],
            "parcel_id": feature["properties"]["parcel_id"],
        }
        for feature in collection["features"]
    ]

    outcomes = evaluate_geospatial_records(records, collection)

    assert outcomes["DEMO-0001"]["geometry_status"] == "valid"
    assert outcomes["DEMO-0001"]["score"] == 100
    assert outcomes["DEMO-0003"]["geometry_status"] == "overlap"
    assert outcomes["DEMO-0004"]["geometry_status"] == "overlap"
    assert outcomes["DEMO-0006"]["geometry_status"] == "invalid"
    assert outcomes["DEMO-0006"]["findings"][0]["severity"] == "critical"


def test_missing_geometry_is_unavailable_not_a_successful_spatial_match():
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"record_id": "R-1", "parcel_id": "P-1"},
                "geometry": None,
            }
        ],
    }

    outcome = evaluate_geospatial_records(
        [{"id": "R-1", "parcel_id": "P-1"}], collection
    )["R-1"]

    assert outcome["score"] is None
    assert outcome["geometry_status"] == "unavailable"

    absent = evaluate_geospatial_records(
        [{"id": "R-2", "parcel_id": "P-2"}], collection
    )["R-2"]
    assert absent["score"] is None
    assert absent["geometry_status"] == "unavailable"


def test_map_features_are_colored_from_validated_outcomes():
    collection = load_geojson("data/demo/parcel_geometries.geojson")
    records = [
        {
            "id": feature["properties"]["record_id"],
            "parcel_id": feature["properties"]["parcel_id"],
        }
        for feature in collection["features"]
    ]

    mapped = styled_geojson(collection, records)
    colors = {
        feature["properties"]["record_id"]: feature["properties"]["map_color"]
        for feature in mapped["features"]
    }

    assert colors["DEMO-0001"] == "#26734d"
    assert colors["DEMO-0003"] == "#c77b18"
    assert colors["DEMO-0006"] == "#a43737"
