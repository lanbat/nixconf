from assistant_router.people import People

NAMES = {"kiril": "Kiril", "maria": "Maria", "ivo": "Ivo"}


def scores(**s):
    base = {k: 0.1 for k in NAMES}
    base.update(s)
    return base


def test_nothing_known_says_nothing():
    p = People(NAMES)
    assert p.line("Kitchen", now=0.0) is None
    assert p.assess("Kitchen", now=0.0).speaker is None


def test_a_clear_voice_names_the_speaker():
    line = People(NAMES).line("Kitchen", now=0.0, voice=scores(kiril=0.75))
    assert "talking to Kiril" in line and "by voice" in line
    assert "never" in line  # the hint is never authority


def test_a_weak_voice_match_is_no_match():
    a = People(NAMES).assess("Kitchen", now=0.0, voice=scores(kiril=0.4))
    assert a.speaker is None


def test_a_voice_like_nobody_enrolled_is_unknown():
    a = People(NAMES).assess("Kitchen", now=0.0, voice=scores())
    assert a.speaker is None


def test_voice_and_aimed_face_agreeing():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "kiril", 0.9, now=10.0)
    a = p.assess("kitchen", now=12.0, voice=scores(kiril=0.7))
    assert a.speaker == "kiril" and a.how == "voice and face" and a.posterior > 0.99


def test_voice_and_aimed_face_disagreeing_names_nobody():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "maria", 0.9, now=10.0)
    line = p.line("Kitchen", now=12.0, voice=scores(kiril=0.8))
    assert "talking to" not in line
    assert "don't know who is speaking" in line


def test_a_face_the_robot_turned_to_is_the_speaker():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "maria", 0.9, now=10.0)
    assert "talking to Maria" in p.line("Kitchen", now=11.0)


def test_a_face_merely_in_the_room_is_nearby_not_the_speaker():
    p = People(NAMES)
    p.observe("Kitchen", "face", "maria", 0.9, now=10.0)
    line = p.line("Kitchen", now=11.0)
    assert "talking to" not in line and "Maria is nearby" in line


def test_presence_narrows_a_close_voice_call():
    # Kiril and Ivo sound alike; only Kiril's phone is here: Kiril.
    p = People(NAMES)
    p.observe("Kitchen", "phone", "kiril", 1.0, now=0.0)
    a = p.assess("Kitchen", now=20.0, voice=scores(kiril=0.62, ivo=0.6))
    assert a.speaker == "kiril"
    # Without the phone the same voice is too close to call.
    assert People(NAMES).assess("Kitchen", now=20.0, voice=scores(kiril=0.62, ivo=0.6)).speaker is None


def test_a_phone_left_elsewhere_does_not_beat_a_clear_voice():
    p = People(NAMES)
    p.observe("Kitchen", "phone", "maria", 1.0, now=0.0)
    a = p.assess("Kitchen", now=20.0, voice=scores(kiril=0.75))
    assert a.speaker == "kiril"
    assert "Maria is nearby" in p.line("Kitchen", now=20.0, voice=scores(kiril=0.75))


def test_the_same_speaker_carries_through_a_conversation():
    p = People(NAMES)
    close = scores(kiril=0.6, ivo=0.58)
    assert p.assess("Kitchen", now=0.0, voice=close).speaker is None
    assert p.assess("Kitchen", now=0.0, voice=close, last="kiril").speaker == "kiril"


def test_two_faces_name_nobody():
    p = People(NAMES)
    p.observe("Kitchen", "face", "maria", 0.9, now=10.0)
    p.observe("Kitchen", "face", "ivo", 0.9, now=10.0)
    line = p.line("Kitchen", now=11.0)
    assert "talking to" not in line
    assert "Maria" in line and "Ivo" in line


def test_evidence_expires():
    p = People(NAMES)
    p.observe("Kitchen", "aimed", "kiril", 0.9, now=0.0)
    p.observe("Kitchen", "phone", "maria", 1.0, now=0.0)
    assert "Kiril" not in p.line("Kitchen", now=20.0)   # the face is 20 s old
    assert "Maria" in p.line("Kitchen", now=20.0)       # the phone still counts
    assert p.line("Kitchen", now=60.0) is None          # so is the phone, after its 25 s window


def test_other_rooms_are_other_rooms():
    p = People(NAMES)
    p.observe("Office", "aimed", "kiril", 0.9, now=0.0)
    assert p.line("Kitchen", now=1.0) is None


def test_unknown_people_and_sources_are_dropped():
    p = People(NAMES)
    p.observe("Kitchen", "face", "mallory", 0.99, now=0.0)
    p.observe("Kitchen", "smell", "kiril", 0.99, now=0.0)
    p.observe("Kitchen", "face", "kiril", "very", now=0.0)
    assert p.line("Kitchen", now=1.0) is None
    assert People(NAMES).assess("Kitchen", now=0.0, voice={"mallory": 0.99}).speaker is None


def test_a_face_that_left_is_forgotten():
    p = People(NAMES)
    p.observe("Kitchen", "face", "kiril", 0.9, now=0.0)
    p.forget_faces("Kitchen")
    assert p.line("Kitchen", now=1.0) is None


def test_who_to_learn_from():
    p = People(NAMES)
    # A clear voice alone: learn.
    assert p.assess("Kitchen", now=0.0, voice=scores(kiril=0.85)).learn == "kiril"
    # A face the robot turned to teaches the voice, even when the voice was unsure.
    p.observe("Kitchen", "aimed", "maria", 0.9, now=0.0)
    assert p.assess("Kitchen", now=1.0, voice=scores(maria=0.5)).learn == "maria"
    # A named speaker on a middling voice alone: not sure enough to learn from.
    q = People(NAMES)
    q.observe("Kitchen", "phone", "kiril", 1.0, now=0.0)
    assert q.assess("Kitchen", now=1.0, voice=scores(kiril=0.62, ivo=0.6)).learn is None


def test_a_tap_window_teaches_nothing_when_someone_else_clearly_speaks():
    p = People(NAMES)
    p.observe("Kitchen", "tap", "kiril", 1.0, now=0.0)
    # Kiril tapped; what he says teaches his voice.
    assert p.assess("Kitchen", now=1.0, voice=scores(kiril=0.3)).learn == "kiril"
    # But this voice is clearly Maria's, enrolled already: she is the one talking
    # in his window, so nothing is learned, whoever the tap says is speaking.
    a = p.assess("Kitchen", now=1.0, voice=scores(kiril=0.3, maria=0.86))
    assert a.learn is None
    # Close, not clear: the tap still teaches.
    assert p.assess("Kitchen", now=1.0, voice=scores(kiril=0.7, maria=0.82)).learn == "kiril"


def test_being_home_tips_a_close_call_but_never_overrules():
    # Kiril sounds a little more like himself than Ivo does: too close to
    # call alone; Kiril's phone on the home network tips it. Home is weaker
    # than a phone in the room (test_presence_narrows_a_close_voice_call).
    close = scores(kiril=0.68, ivo=0.6)
    assert People(NAMES).assess("Kitchen", now=10.0, voice=close).speaker is None
    p = People(NAMES)
    p.observe_home(["kiril", "ghost"], now=0.0)
    assert p.home(now=10.0) == {"kiril"}  # unknown keys are dropped
    a = p.assess("Kitchen", now=10.0, voice=close)
    assert a.speaker == "kiril"
    # Home is not nearby: it says nothing about this room.
    assert a.nearby == []
    # A clear voice of someone whose phone is away still wins.
    assert p.assess("Kitchen", now=10.0, voice=scores(ivo=0.75)).speaker == "ivo"


def test_home_is_replaced_and_expires():
    p = People(NAMES)
    p.observe_home(["kiril", "maria"], now=0.0)
    p.observe_home(["maria"], now=60.0)
    assert p.home(now=61.0) == {"maria"}
    assert p.home(now=60.0 + 181) == set()  # the house sensor went quiet
    p.observe_home("kiril", now=300.0)  # not a list: ignored
    assert p.home(now=300.0) == set()


def test_a_phone_heard_through_a_wall_counts_where_it_is_loudest():
    p = People(NAMES)
    p.observe_phone("Kitchen", "kiril", -60, now=0.0)
    p.observe_phone("Bedroom 1", "kiril", -80, now=0.0)  # the same phone, through the wall
    assert "Kiril" in p.line("Kitchen", now=10.0)
    assert p.line("Bedroom 1", now=10.0) is None
    # Close enough in both: in both (a doorway, a thin wall).
    p.observe_phone("Bedroom 1", "kiril", -64, now=20.0)
    assert "Kiril" in p.line("Bedroom 1", now=24.0)
    # Gone quiet in the kitchen: the bedroom alone has it.
    assert "Kiril" in p.line("Bedroom 1", now=35.0)
    assert p.line("Kitchen", now=35.0) is None


def test_a_noisy_neighbour_does_not_move_them_between_rooms():
    p = People(NAMES)
    p.observe_phone("Office", "kiril", -70, now=0.0)
    assert p.where("kiril", now=1.0)[0] == "Office"
    # The bedroom's sensor is a couple of dB louder now and then: inside the noise.
    p.observe_phone("Bedroom 1", "kiril", -68, now=2.0)
    p.observe_phone("Office", "kiril", -71, now=2.0)
    assert p.where("kiril", now=3.0)[0] == "Office"
    p.observe_phone("Bedroom 1", "kiril", -69, now=4.0)
    p.observe_phone("Office", "kiril", -72, now=4.0)
    assert p.where("kiril", now=5.0)[0] == "Office"
    # Clearly louder, but within the through-wall margin: it must keep leading.
    p.observe_phone("Bedroom 1", "kiril", -64, now=6.0)
    p.observe_phone("Office", "kiril", -70, now=6.0)
    assert p.where("kiril", now=7.0)[0] == "Office"
    p.observe_phone("Bedroom 1", "kiril", -64, now=12.0)
    p.observe_phone("Office", "kiril", -70, now=12.0)
    assert p.where("kiril", now=13.0)[0] == "Bedroom 1"  # six seconds of leading


def test_a_face_in_the_other_room_moves_them_at_once():
    p = People(NAMES)
    p.observe_phone("Office", "kiril", -70, now=0.0)
    assert p.where("kiril", now=1.0)[0] == "Office"
    p.observe("Bedroom 1", "face", "kiril", 0.9, now=2.0)
    assert p.where("kiril", now=3.0)[0] == "Bedroom 1"


def test_going_quiet_in_the_old_room_moves_them_at_once():
    p = People(NAMES)
    p.observe_phone("Office", "kiril", -70, now=0.0)
    assert p.where("kiril", now=1.0)[0] == "Office"
    p.observe_phone("Bedroom 1", "kiril", -64, now=2.0)  # through the wall, louder
    assert p.where("kiril", now=3.0)[0] == "Office"
    # The office stops hearing it; the bedroom still does.
    p.observe_phone("Bedroom 1", "kiril", -64, now=30.0)
    assert p.where("kiril", now=31.0)[0] == "Bedroom 1"


def test_bluetooth_and_wifi_strengths_are_not_compared():
    p = People(NAMES)
    p.observe_phone("Kitchen", "maria", -85, now=0.0, via="wifi")
    p.observe_phone("Bedroom 1", "maria", -55, now=0.0, via="ble")
    assert "Maria" in p.line("Kitchen", now=1.0)
    assert "Maria" in p.line("Bedroom 1", now=1.0)


def test_strength_must_be_a_number_and_a_person():
    p = People(NAMES)
    p.observe_phone("Kitchen", "kiril", "loud", now=0.0)
    p.observe_phone("Kitchen", "ghost", -50, now=0.0)
    assert p.line("Kitchen", now=1.0) is None


def test_a_card_names_its_owner_unless_the_voice_disagrees():
    p = People(NAMES)
    p.observe("Kitchen", "card", "kiril", 1.0, now=0.0)
    a = p.assess("Kitchen", now=30.0)
    assert a.speaker == "kiril" and a.how == "card"
    # Someone else, clearly by voice, is still them.
    assert p.assess("Kitchen", now=30.0, voice=scores(ivo=0.75)).speaker == "ivo"
    # Two minutes later the card is forgotten.
    assert p.assess("Kitchen", now=130.0).speaker is None
    # Another room knows nothing of it.
    assert p.assess("Bedroom 1", now=30.0).speaker is None


def test_evidence_shows_what_each_source_says():
    p = People({"kiril": "Kiril", "maria": "Maria"})
    p.observe("Kitchen", "face", "maria", 0.9, 100.0)
    p.observe("Kitchen", "aimed", "kiril", 0.8, 100.0)
    p.observe_phone("Kitchen", "kiril", -60, 100.0, via="ble")
    p.observe_phone("Bedroom", "kiril", -80, 100.0, via="ble")  # louder in the Kitchen
    p.observe("Kitchen", "tap", "kiril", 1.0, 100.0)
    p.observe_home(["maria"], 100.0)
    p.note_voice("Kitchen", {"kiril": 0.71, "maria": 0.2, "stranger": 0.9}, 100.0)
    e = p.evidence("Kitchen", 101.0)
    assert e["camera"] == {"maria": 0.9} and e["robot"] == {"kiril": 0.8}
    assert e["bluetooth"] == {"kiril": 1.0} and e["wifi"] == {}
    assert e["card"] == {"kiril": 1.0} and e["home"] == {"maria": 1.0}
    assert e["voice"] == {"kiril": 0.71}  # the best enrolled match only
    assert p.evidence("Bedroom", 101.0)["bluetooth"] == {}  # not where it's loudest


def test_a_clear_voice_puts_them_in_the_room_for_five_minutes():
    p = People(NAMES)
    p.note_voice("Kitchen", scores(kiril=0.8), now=100.0)
    assert p.presence("Kitchen", 101.0)["kiril"] == (0.8, ["voice"])
    assert "kiril" in p.here("Kitchen", 101.0) and p.here("Bedroom 1", 101.0) == {}
    assert "kiril" not in p.presence("Kitchen", 100.0 + 301)
    # Too weak, or too close to someone else's: nobody is placed.
    q = People(NAMES)
    q.note_voice("Kitchen", scores(kiril=0.65), now=100.0)
    q.note_voice("Hall", scores(kiril=0.8, maria=0.7), now=100.0)
    assert q.presence("Kitchen", 101.0) == {} and q.presence("Hall", 101.0) == {}


def test_a_voice_heard_counts_for_the_next_request():
    p = People(NAMES)
    p.note_voice("Kitchen", scores(maria=0.85), now=100.0)
    a = p.assess("Kitchen", now=160.0)  # a sentence voice-id has no word on
    assert a.speaker is None and a.nearby == ["maria"]
    # It tips a close call her way, as a face in the room would.
    close = scores(maria=0.55, ivo=0.5)
    assert p.assess("Kitchen", now=160.0, voice=close).speaker == "maria"
    assert People(NAMES).assess("Kitchen", now=160.0, voice=close).speaker is None


def test_presence_puts_the_sources_together():
    p = People(NAMES)
    p.observe("Kitchen", "tap", "kiril", 1.0, now=100.0)
    p.note_voice("Kitchen", scores(kiril=0.8), now=100.0)
    p.observe_phone("Kitchen", "maria", -60, 100.0, via="wifi")
    p.observe_home(["maria", "ivo"], now=100.0)
    found = p.presence("Kitchen", 101.0)
    assert found["kiril"] == (round(1 - 0.02 * 0.2, 3), ["card", "voice"])  # either would do
    assert found["maria"] == (round(1 - 0.2 * 0.8, 3), ["home", "wifi"])
    assert found["ivo"] == (0.2, ["home"])
    assert set(p.here("Kitchen", 101.0)) == {"kiril", "maria"}  # home alone isn't here


def test_where_is_the_room_they_most_likely_are_in():
    p = People(NAMES)
    p.observe_phone("Kitchen", "kiril", -60, 100.0, via="ble")
    p.note_voice("Bedroom 1", scores(kiril=0.9), now=100.0)
    assert p.where("kiril", 101.0) == ("Bedroom 1", 0.9, ["voice"])
    p.observe_phone("Kitchen", "kiril", -60, 400.0, via="ble")  # the voice is old now
    assert p.where("kiril", 401.0) == ("Kitchen", 0.8, ["bluetooth"])
    p.observe_home(["maria"], now=401.0)
    assert p.where("maria", 402.0) == ("home", 0.2, ["home"])
    assert p.where("ivo", 402.0) == ("away", 0.0, [])


def test_where_prefers_the_loudest_room_when_two_are_placed():
    # Two rooms hear the phone (a doorway, a thin wall) and both fall within the
    # 6 dB margin, so both count with the same confidence. The louder one is where
    # the person is — not the one that happens to sort first alphabetically.
    p = People(NAMES)
    p.observe_phone("Bedroom 1", "kiril", -64, now=0.0)
    p.observe_phone("Office", "kiril", -60, now=0.0)
    assert p.where("kiril", now=1.0)[0] == "Office"

    q = People(NAMES)
    q.observe_phone("Bedroom 1", "kiril", -60, now=0.0)
    q.observe_phone("Office", "kiril", -64, now=0.0)
    assert q.where("kiril", now=1.0)[0] == "Bedroom 1"


def test_a_tap_or_a_card_teaches_their_voice_for_two_minutes():
    p = People(NAMES)
    p.observe("Kitchen", "tap", "maria", 1.0, now=100.0)
    a = p.assess("Kitchen", now=110.0)
    assert a.speaker == "maria" and a.learn == "maria"
    assert p.bound("Kitchen", 110.0) == ("maria", 100.0)
    p.observe("Kitchen", "card", "ivo", 1.0, now=150.0)
    assert p.bound("Kitchen", 160.0) == ("ivo", 150.0)  # the latest
    assert p.bound("Kitchen", 271.0) == (None, 0.0) and p.bound("Hall", 160.0) == (None, 0.0)


def test_old_or_unclear_voice_is_not_shown():
    p = People({"kiril": "Kiril"})
    p.note_voice("Kitchen", {"kiril": 0.1}, 100.0)
    assert p.evidence("Kitchen", 101.0)["voice"] == {}  # no better than a stranger
    p.note_voice("Kitchen", {"kiril": 0.8}, 100.0)
    assert p.evidence("Kitchen", 100.0 + 301)["voice"] == {}  # five minutes on
