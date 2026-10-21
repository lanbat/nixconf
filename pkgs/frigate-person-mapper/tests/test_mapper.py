"""Unit tests for the mapper's pure core (parsing, state, payloads)."""
from __future__ import annotations

import json

from frigate_person_mapper import frigate, ha, router
from frigate_person_mapper.state import PersonState


# -- frigate: tracked_object_update ------------------------------------------

def test_face_update_parses_a_named_face():
    payload = json.dumps({
        "type": "face",
        "id": "1717000000.0-abc123",
        "name": "alex",
        "score": 0.95,
        "camera": "living",
        "timestamp": 1717000000.5,
    })
    upd = frigate.parse_tracked_object_update("frigate/tracked_object_update", payload)
    assert upd == frigate.FaceUpdate("living", "1717000000.0-abc123", "alex", 0.95, 1717000000.5)


def test_face_update_parses_an_unnamed_face():
    payload = json.dumps({"type": "face", "id": "e1", "name": None, "score": 0.4, "camera": "hall"})
    upd = frigate.parse_tracked_object_update("frigate/tracked_object_update", payload)
    assert upd is not None
    assert upd.name is None
    assert upd.score == 0.4


def test_face_update_ignores_other_object_types():
    payload = json.dumps({"type": "person", "id": "e1", "name": None, "score": 0.9, "camera": "hall"})
    assert frigate.parse_tracked_object_update("frigate/tracked_object_update", payload) is None


def test_face_update_ignores_other_topics_and_bad_payloads():
    payload = json.dumps({"type": "face", "id": "e1", "name": "alex", "score": 0.9, "camera": "hall"})
    assert frigate.parse_tracked_object_update("frigate/events", payload) is None
    assert frigate.parse_tracked_object_update("frigate/tracked_object_update", "not json") is None
    assert frigate.parse_tracked_object_update("frigate/tracked_object_update", "{}") is None


def test_face_update_requires_camera_and_id():
    assert frigate.parse_tracked_object_update(
        "frigate/tracked_object_update", json.dumps({"type": "face", "id": "e1", "score": 0.9, "camera": ""})
    ) is None
    assert frigate.parse_tracked_object_update(
        "frigate/tracked_object_update", json.dumps({"type": "face", "name": "alex", "score": 0.9, "camera": "hall"})
    ) is None


# -- frigate: events ----------------------------------------------------------

def test_event_end_parses_once_end_time_is_set():
    payload = json.dumps({
        "type": "end",
        "before": {"id": "e1"},
        "after": {"id": "e1", "camera": "living", "end_time": 1717000010.0},
    })
    end = frigate.parse_events("frigate/events", payload)
    assert end == frigate.EventEnd("living", "e1")


def test_event_end_ignores_a_placeholder_end_without_end_time():
    payload = json.dumps({"type": "end", "before": {"id": "e1"}, "after": {"id": "e1", "camera": "living"}})
    assert frigate.parse_events("frigate/events", payload) is None


def test_event_end_ignores_other_event_types():
    payload = json.dumps({"type": "new", "before": {}, "after": {"id": "e1", "camera": "living", "end_time": 1.0}})
    assert frigate.parse_events("frigate/events", payload) is None
    assert frigate.parse_events("frigate/tracked_object_update", json.dumps({"type": "end"})) is None


# -- frigate: camera status + availability ------------------------------------

def test_camera_status_parses_online_and_offline():
    assert frigate.parse_camera_status("frigate/living/status/detect", "online") == frigate.CameraStatus("living", True)
    assert frigate.parse_camera_status("frigate/living/status/detect", "offline") == frigate.CameraStatus("living", False)
    assert frigate.parse_camera_status("frigate/living/status/detect", "disabled") == frigate.CameraStatus("living", False)


def test_camera_status_ignores_other_topics():
    assert frigate.parse_camera_status("frigate/living/status", "online") is None
    assert frigate.parse_camera_status("frigate/available", "online") is None


def test_availability_parses_known_states():
    assert frigate.parse_availability("online") is True
    assert frigate.parse_availability("stopped") is False
    assert frigate.parse_availability("offline") is False
    assert frigate.parse_availability("something else") is None


# -- state: who is in which room ----------------------------------------------

def make_state():
    return PersonState({"alex", "bob"}, {"cam-living": "living", "cam-hall": "hall", "cam-living-2": "living"})


def test_a_named_face_lands_in_its_room():
    state = make_state()
    state.on_face_update("cam-living", "e1", "alex", 0.9)
    snap = state.snapshot("living")
    assert snap.faces == (("alex", 0.9),)
    assert snap.unknown == 0


def test_an_unnamed_face_is_unknown_not_a_person():
    state = make_state()
    state.on_face_update("cam-living", "e1", None, 0.9)
    snap = state.snapshot("living")
    assert snap.faces == ()
    assert snap.unknown == 1


def test_a_face_not_in_the_registry_is_unknown():
    state = make_state()
    state.on_face_update("cam-living", "e1", "stranger", 0.99)
    snap = state.snapshot("living")
    assert snap.faces == ()
    assert snap.unknown == 1


def test_multiple_people_and_cameras_collapse_per_room():
    state = make_state()
    state.on_face_update("cam-living", "e1", "alex", 0.9)
    state.on_face_update("cam-living-2", "e2", "alex", 0.7)  # same person, other camera, lower
    state.on_face_update("cam-living", "e3", "bob", 0.8)
    snap = state.snapshot("living")
    # alex collapses to the best score across cameras; bob is separate.
    assert snap.faces == (("alex", 0.9), ("bob", 0.8))
    assert state.snapshot("hall").faces == ()


def test_an_event_end_drops_the_person():
    state = make_state()
    state.on_face_update("cam-living", "e1", "alex", 0.9)
    state.on_face_update("cam-living", "e2", "bob", 0.8)
    assert state.snapshot("living").faces == (("alex", 0.9), ("bob", 0.8))
    state.on_event_end("cam-living", "e1")
    assert state.snapshot("living").faces == (("bob", 0.8),)
    state.on_event_end("cam-living", "e2")
    assert state.snapshot("living").faces == ()


def test_an_end_before_any_update_is_a_noop():
    state = make_state()
    state.on_event_end("cam-living", "nope")
    assert state.snapshot("living").faces == ()


def test_unknown_cameras_are_ignored():
    state = make_state()
    state.on_face_update("cam-outside", "e1", "alex", 0.9)  # not mapped to a room
    assert state.snapshot("living").faces == ()
    assert state.rooms == ("hall", "living")


def test_camera_status_drives_room_availability():
    state = make_state()
    assert state.snapshot("living").camera_online is False
    state.on_camera_status("cam-living", True)
    assert state.snapshot("living").camera_online is True
    # an unmapped camera does not affect the room
    state.on_camera_status("cam-outside", True)
    assert state.snapshot("hall").camera_online is False


# -- ha: discovery + state + event --------------------------------------------

def test_discovery_configs_cover_the_five_entities():
    configs = ha.discovery_configs("living room")
    topics = [topic for topic, _ in configs]
    keys = _cfg_keys(configs)
    assert keys == {
        "homeassistant/sensor/living_room_person/config",
        "homeassistant/number/living_room_confidence/config",
        "homeassistant/binary_sensor/living_room_present/config",
        "homeassistant/binary_sensor/living_room_known/config",
        "homeassistant/binary_sensor/living_room_camera/config",
    }
    assert all("unique_id" in cfg for _, cfg in configs)
    assert "state_topic" in dict((t, c) for t, c in configs)["homeassistant/sensor/living_room_person/config"]


def _cfg_keys(configs):
    return set(topic for topic, _ in configs)


def test_state_values_for_a_known_person():
    values = dict(ha.state_values("living", (("alex", 0.93),), 0, True))
    assert values == {
        "homelab/person/living/person": "alex",
        "homelab/person/living/confidence": "0.930",
        "homelab/person/living/present": "ON",
        "homelab/person/living/known": "ON",
        "homelab/person/living/camera": "ON",
    }


def test_state_values_when_only_an_unknown_face_is_present():
    values = dict(ha.state_values("living", (), 1, True))
    assert values["homelab/person/living/person"] == "unknown"
    assert values["homelab/person/living/present"] == "ON"
    assert values["homelab/person/living/known"] == "OFF"


def test_state_values_when_noone_is_present():
    values = dict(ha.state_values("living", (), 0, False))
    assert values["homelab/person/living/person"] == "none"
    assert values["homelab/person/living/present"] == "OFF"
    assert values["homelab/person/living/known"] == "OFF"
    assert values["homelab/person/living/camera"] == "OFF"


def test_state_values_report_the_most_confident_person():
    values = dict(ha.state_values("living", (("alex", 0.7), ("bob", 0.95)), 0, True))
    assert values["homelab/person/living/person"] == "bob"
    assert values["homelab/person/living/confidence"] == "0.950"


def test_event_payload_names_the_top_face():
    event = ha.event_payload("living", (("alex", 0.93),), 1, 1717000000.0)
    assert event == {
        "room": "living",
        "person": "alex",
        "confidence": 0.93,
        "known": True,
        "unknown": 1,
        "at": 1717000000.0,
    }


def test_event_payload_for_an_empty_room():
    event = ha.event_payload("living", (), 0, 1717000000.0)
    assert event["person"] is None
    assert event["confidence"] is None
    assert event["known"] is False


# -- router: body-socket messages ---------------------------------------------

def test_hello_names_the_room_and_kind():
    assert router.hello("living") == {"hello": {"room": "living", "kind": "face-source", "proto": router.PROTO}}


def test_faces_message_uses_the_router_schema():
    assert router.faces((("alex", 0.93), ("bob", 0.8))) == {
        "faces": [
            {"person": "alex", "confidence": 0.93},
            {"person": "bob", "confidence": 0.8},
        ]
    }
    assert router.faces(()) == {"faces": []}


def test_without_a_registry_anyone_the_library_names_is_named():
    state = PersonState(None, {"c1": "Driveway"})
    state.on_face_update("c1", "e1", "akadmin", 0.82)
    state.on_face_update("c1", "e2", "unknown", 0.9)  # Frigate's word for nobody
    state.on_face_update("c1", "e3", "../x", 0.9)  # not a person key
    snap = state.snapshot("Driveway")
    assert snap.faces == (("akadmin", 0.82),) and snap.unknown == 2
    assert snap.events == {"akadmin": "e1"}


def test_each_face_carries_the_event_of_its_best_sighting():
    state = make_state()
    state.on_face_update("cam-living", "e1", "alex", 0.7)
    state.on_face_update("cam-living-2", "e2", "alex", 0.9)
    snap = state.snapshot("living")
    assert snap.events == {"alex": "e2"}
    assert router.faces(snap.faces, snap.events) == {"faces": [{"person": "alex", "confidence": 0.9, "event": "e2"}]}


def test_the_cameras_are_configured_room_to_camera():
    from frigate_person_mapper.__main__ import cameras_by_name
    assert cameras_by_name({"Driveway": "c1", "Living Room": "front", "Bad": ""}) == {
        "c1": "Driveway", "front": "Living Room"}
