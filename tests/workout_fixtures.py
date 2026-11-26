"""Shared synthetic Endomondo records for pipeline tests."""


def sources() -> tuple[dict, dict]:
    """Create known route and metadata fixtures.

    Input: None.
    Output: Matching HR and Meta dictionaries.
    """
    hr = {
        "id": 1,
        "userId": 2,
        "sport": "run",
        "latitude": [0, 0, 0],
        "longitude": [0, 0.01, 0.03],
        "altitude": [0, 10, 30],
        "timestamp": [1_400_000_000, 1_400_000_010, 1_400_000_030],
    }
    meta = {
        "id": 1,
        "userId": 2,
        "sport": "run",
        "distance": 5,
        "duration": 1800,
        "ascent": 30,
        "descent": 0,
    }
    return hr, meta
